# Metrics

- [Metrics](#metrics)
    - [Authentication](#authentication)
    - [Runtime Metrics](#runtime-metrics)
    - [Proxy Metrics](#proxy-metrics)
      - [avenrafault_proxy_received_bytes_total / avenrafault_proxy_sent_bytes_total](#avenrafault_proxy_received_bytes_total--avenrafault_proxy_sent_bytes_total)

### Authentication

When the server runs with `AVENRA_FAULT_AUTH_TOKEN` set, `/metrics` requires an
`Authorization: Bearer <token>` header like every other endpoint. For Prometheus, set
`authorization.credentials` (or `authorization.credentials_file`) in the scrape config.

### Runtime Metrics

To enable runtime metrics related to the state of the go runtime, build version, process info, use the `-runtime-metrics` flag.

For more details, see below:
- [NewGoCollector](https://pkg.go.dev/github.com/prometheus/client_golang/prometheus/collectors#NewGoCollector)
- [NewBuildInfoCollector](https://pkg.go.dev/github.com/prometheus/client_golang/prometheus/collectors#NewBuildInfoCollector)
- [NewProcessCollector](https://pkg.go.dev/github.com/prometheus/client_golang/prometheus/collectors#NewProcessCollector)

### Proxy Metrics

To enable metrics related to avenrafault internals, use the `-proxy-metrics` flag.
#### avenrafault_proxy_received_bytes_total / avenrafault_proxy_sent_bytes_total

The total number of bytes received/sent on a given proxy link in a given direction

```mermaid
sequenceDiagram
    Client->>+Avenrafault: avenrafault_proxy_received_bytes_total{direction="upstream"}
    Avenrafault->>+Server: avenrafault_proxy_sent_bytes_total{direction="upstream"}
    Server->>+Avenrafault: avenrafault_proxy_received_bytes_total{direction="downstream"}
    Avenrafault->>+Client: avenrafault_proxy_sent_bytes_total{direction="downstream"}
```

**Type**

Counter

**Labels**

| Label     | Description                    | Example               |
|-----------|--------------------------------|-----------------------|
| direction | Direction of the link          | upstream / downstream |
| listener  | Listener address of this proxy | 0.0.0.0:8080          |
| proxy     | Proxy name                     | my-proxy              |
| upstream  | Upstream address of this proxy | httpbin.org:80        |

