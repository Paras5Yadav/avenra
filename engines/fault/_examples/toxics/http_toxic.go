// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
// Ported from https://github.com/xthexder/toxic-example/blob/master/http.go
package main

import (
	"bufio"
	"bytes"
	"io"
	"net/http"
	"os"


	"github.com/prometheus/client_golang/prometheus"
	"github.com/rs/zerolog"


	"avenra.local/fault/v2"
	"avenra.local/fault/v2/stream"
	"avenra.local/fault/v2/toxics"
)

type HttpToxic struct{}

func (t *HttpToxic) ModifyResponse(resp *http.Response) {
	resp.Header.Set("Location", "https://avenra.local/fault")
}

func (t *HttpToxic) Pipe(stub *toxics.ToxicStub) {
	buffer := bytes.NewBuffer(make([]byte, 0, 32*1024))
	writer := stream.NewChanWriter(stub.Output)
	reader := stream.NewChanReader(stub.Input)
	reader.SetInterrupt(stub.Interrupt)
	for {
		tee := io.TeeReader(reader, buffer)
		resp, err := http.ReadResponse(bufio.NewReader(tee), nil)
		if err == stream.ErrInterrupted {
			buffer.WriteTo(writer)
			return
		} else if err == io.EOF {
			stub.Close()
			return
		}
		if err != nil {
			buffer.WriteTo(writer)
		} else {
			t.ModifyResponse(resp)
			resp.Write(writer)
		}
		buffer.Reset()
	}
}

func main() {
	toxics.Register("http", new(HttpToxic))

	logger := zerolog.New(os.Stderr).With().Caller().Timestamp().Logger()
	metrics := avenrafault.NewMetricsContainer(prometheus.NewRegistry())
	server := avenrafault.NewServer(metrics, logger)
	server.Listen("0.0.0.0:8484")
}
