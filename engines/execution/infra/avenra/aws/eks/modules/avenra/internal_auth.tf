resource "aws_secretsmanager_secret" "avenra_internal" {
  name = "${local.name}-avenra-internal"

  tags = var.common_tags
}

resource "tls_private_key" "avenra_internal" {
  algorithm = "ED25519"
}

resource "tls_private_key" "avenra_internal_control" {
  algorithm = "ED25519"
}

resource "tls_private_key" "avenra_public_cursor" {
  algorithm = "ED25519"
}

resource "tls_private_key" "avenra_api_route_token_v2" {
  algorithm = "ED25519"
}

resource "tls_private_key" "avenra_ecw_credential" {
  algorithm = "ED25519"
}

resource "aws_secretsmanager_secret_version" "avenra_internal" {
  secret_id = aws_secretsmanager_secret.avenra_internal.id
  secret_string = jsonencode({
    AVENRA_INTERNAL_TOKEN                   = base64sha256(tls_private_key.avenra_internal.private_key_pem)
    AVENRA_INTERNAL_CONTROL_TOKEN           = base64sha256(tls_private_key.avenra_internal_control.private_key_pem)
    AVENRA_PUBLIC_CURSOR_SECRET             = base64sha256(tls_private_key.avenra_public_cursor.private_key_pem)
    AVENRA_API_ROUTE_ED25519_SIGNING_KEY    = tls_private_key.avenra_api_route_token_v2.private_key_pem
    AVENRA_API_ROUTE_ED25519_SIGNING_KEY_ID = "route-v2-1"
    AVENRA_API_ROUTE_ED25519_ISSUER         = "${local.name}-route-authority"
    AVENRA_API_ROUTE_ED25519_VERIFICATION_BUNDLE = jsonencode({
      generation = 1
      issuer     = "${local.name}-route-authority"
      keys = [{
        key_id         = "route-v2-1"
        public_key_pem = tls_private_key.avenra_api_route_token_v2.public_key_pem
      }]
    })
    AVENRA_ECW_CREDENTIAL_KEY = base64sha256(tls_private_key.avenra_ecw_credential.private_key_pem)
  })
}
