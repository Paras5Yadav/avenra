// Independently parse and authenticate the owned XML contract using only Go's
// standard library. No application parser, schema or signing helper is used.
package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"regexp"
	"strconv"
	"strings"
)

const namespace = "urn:avenra:upi:wire:1"

var reference = regexp.MustCompile(`^[A-Za-z0-9_.:@/\-]{1,160}$`)
var hash = regexp.MustCompile(`^[0-9a-f]{64}$`)
var integer = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)
var number = regexp.MustCompile(`^(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$`)

type Input struct {
	XML      string `json:"xml_base64"`
	Public   string `json:"public_key"`
	Contract string `json:"contract_hash"`
	Profile  string `json:"profile_hash"`
}

func attributes(element xml.StartElement, allowed []string, optional map[string]bool) (map[string]string, error) {
	result := map[string]string{}
	for _, a := range element.Attr {
		if (a.Name.Space == "xmlns" || (a.Name.Space == "" && a.Name.Local == "xmlns")) && a.Value == namespace {
			continue
		}
		if a.Name.Space != "" {
			return nil, errors.New("namespaced attribute")
		}
		name := a.Name.Local
		if _, exists := result[name]; exists {
			return nil, errors.New("duplicate attribute")
		}
		found := false
		for _, known := range allowed {
			if known == name {
				found = true
			}
		}
		if !found {
			return nil, errors.New("unknown attribute")
		}
		result[name] = a.Value
	}
	for _, name := range allowed {
		if _, exists := result[name]; !exists && !optional[name] {
			return nil, errors.New("missing attribute")
		}
	}
	return result, nil
}

func read(input Input) (map[string]any, error) {
	raw, err := base64.StdEncoding.Strict().DecodeString(input.XML)
	if err != nil || len(raw) == 0 || len(raw) > 8192 {
		return nil, errors.New("size or encoding")
	}
	decoder := xml.NewDecoder(bytes.NewReader(raw))
	depth, stage := 0, 0
	var header, payload map[string]string
	var signature strings.Builder
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, errors.New("malformed XML")
		}
		switch t := token.(type) {
		case xml.StartElement:
			if t.Name.Space != namespace {
				return nil, errors.New("namespace")
			}
			if depth == 0 {
				if stage != 0 || t.Name.Local != "Message" {
					return nil, errors.New("root")
				}
				a, err := attributes(t, []string{"version", "contractHash"}, nil)
				if err != nil || a["version"] != "1" || a["contractHash"] != input.Contract || !hash.MatchString(input.Contract) {
					return nil, errors.New("contract version")
				}
				stage = 1
			} else if depth == 1 {
				switch stage {
				case 1:
					if t.Name.Local != "Header" {
						return nil, errors.New("header order")
					}
					header, err = attributes(t, []string{"format", "profileHash", "tenantId", "environmentId", "paymentId", "messageId", "step", "generation", "sender", "recipient", "kind", "keyId", "issuedAt", "expiresAt"}, nil)
				case 2:
					if t.Name.Local != "Payload" {
						return nil, errors.New("payload order")
					}
					payload, err = attributes(t, []string{"intentHash", "amountPaise", "currency", "operationId", "outcome"}, map[string]bool{"operationId": true, "outcome": true})
				case 3:
					if t.Name.Local != "Authentication" {
						return nil, errors.New("authentication order")
					}
					var a map[string]string
					a, err = attributes(t, []string{"algorithm"}, nil)
					if err == nil && a["algorithm"] != "Ed25519-canonical-json" {
						err = errors.New("signature algorithm")
					}
				default:
					return nil, errors.New("extra element")
				}
				if err != nil {
					return nil, err
				}
			} else {
				return nil, errors.New("nested/wrapped elements")
			}
			depth++
		case xml.EndElement:
			depth--
			if depth == 1 {
				stage++
			}
			if depth == 0 {
				if stage != 4 {
					return nil, errors.New("incomplete message")
				}
				stage = 5
			}
		case xml.CharData:
			if depth == 2 && stage == 3 {
				signature.Write([]byte(t))
			} else if strings.TrimSpace(string(t)) != "" {
				return nil, errors.New("unexpected text")
			}
		default:
			return nil, errors.New("DTD, comment or processing instruction")
		}
	}
	if depth != 0 || stage != 5 {
		return nil, errors.New("incomplete document")
	}
	if header["format"] != "avenra-payment-message/1" || header["profileHash"] != input.Profile || !hash.MatchString(input.Profile) {
		return nil, errors.New("flow profile")
	}
	fields := map[string]string{"tenantId": "tenant_id", "environmentId": "environment_id", "paymentId": "payment_id", "messageId": "message_id", "sender": "sender", "recipient": "recipient", "keyId": "key_id"}
	document := map[string]any{"format": header["format"], "profile_hash": header["profileHash"], "kind": header["kind"]}
	for attr, key := range fields {
		if !reference.MatchString(header[attr]) {
			return nil, errors.New("identity")
		}
		document[key] = header[attr]
	}
	step, err := strconv.Atoi(header["step"])
	kinds := []string{"collect.request", "collect.present", "payment.submit", "debit.request", "debit.response", "credit.request", "credit.response", "payment.result", "merchant.notification"}
	if err != nil || step < -1 || step > 7 || strconv.Itoa(step) != header["step"] || header["kind"] != kinds[step+1] {
		return nil, errors.New("route")
	}
	if header["sender"] == header["recipient"] || !strings.HasPrefix(header["keyId"], header["sender"]+"_k") {
		return nil, errors.New("key route")
	}
	generation, err := strconv.Atoi(header["generation"])
	if err != nil || generation < 0 || generation > 1000000 || !integer.MatchString(header["generation"]) {
		return nil, errors.New("generation")
	}
	document["step"], document["generation"] = json.Number(header["step"]), json.Number(header["generation"])
	times := []float64{}
	for _, pair := range [][2]string{{"issuedAt", "issued_at"}, {"expiresAt", "expires_at"}} {
		text := header[pair[0]]
		value, err := strconv.ParseFloat(text, 64)
		if err != nil || len(text) > 32 || !number.MatchString(text) || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 || value > 9007199254740991 {
			return nil, errors.New("time")
		}
		times = append(times, value)
		document[pair[1]] = json.Number(text)
	}
	if times[1]-times[0] != 300 {
		return nil, errors.New("validity interval")
	}
	amount, err := strconv.ParseInt(payload["amountPaise"], 10, 64)
	if err != nil || amount < 1 || amount > 100000000 || !integer.MatchString(payload["amountPaise"]) || payload["currency"] != "INR" || !hash.MatchString(payload["intentHash"]) {
		return nil, errors.New("financial payload")
	}
	operation, hasOperation := payload["operationId"]
	outcome, hasOutcome := payload["outcome"]
	if hasOperation != (step >= 1) || (hasOperation && !reference.MatchString(operation)) {
		return nil, errors.New("operation")
	}
	validOutcome := !hasOutcome && step <= 2
	if step == 3 {
		validOutcome = outcome == "confirmed" || outcome == "declined"
	}
	if step == 4 {
		validOutcome = outcome == "confirmed"
	}
	if step >= 5 {
		validOutcome = outcome == "succeeded" || outcome == "declined" || outcome == "reversed"
	}
	if !validOutcome {
		return nil, errors.New("outcome")
	}
	values := map[string]any{"intent_hash": payload["intentHash"], "amount_paise": json.Number(payload["amountPaise"]), "currency": "INR", "operation_id": nil, "outcome": nil}
	if hasOperation {
		values["operation_id"] = operation
	}
	if hasOutcome {
		values["outcome"] = outcome
	}
	document["payload"] = values
	encoded, err := json.Marshal(document)
	if err != nil {
		return nil, err
	}
	public, keyerr := hex.DecodeString(input.Public)
	sig, sigerr := hex.DecodeString(signature.String())
	if keyerr != nil || sigerr != nil || len(public) != ed25519.PublicKeySize || len(sig) != ed25519.SignatureSize || !ed25519.Verify(ed25519.PublicKey(public), encoded, sig) {
		return nil, errors.New("signature")
	}
	sum := sha256.Sum256(encoded)
	return map[string]any{"accepted": true, "signature_valid": true, "document_hash": hex.EncodeToString(sum[:]), "message_id": header["messageId"], "amount_paise": amount, "contract_hash": input.Contract}, nil
}

func main() {
	decoder := json.NewDecoder(io.LimitReader(os.Stdin, 4*1024*1024))
	decoder.DisallowUnknownFields()
	var inputs []Input
	if err := decoder.Decode(&inputs); err != nil || len(inputs) == 0 || len(inputs) > 256 {
		fmt.Fprintln(os.Stderr, "bounded oracle input required")
		os.Exit(2)
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		fmt.Fprintln(os.Stderr, "trailing input")
		os.Exit(2)
	}
	results := []map[string]any{}
	for _, input := range inputs {
		result, err := read(input)
		if err != nil {
			result = map[string]any{"accepted": false, "error": err.Error()}
		}
		results = append(results, result)
	}
	if err := json.NewEncoder(os.Stdout).Encode(results); err != nil {
		os.Exit(2)
	}
}
