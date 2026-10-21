"""Declarative release capabilities, read as literals without importing a candidate."""

CAPABILITIES = {
    'format': 'avenra-deployment-capabilities/1',
    'engine_bindings': ['avenra-loopback-engines/1'],
    'bank_routes': ['avenra-bank-recovery-routes/1'],
    'aa_routes': ['avenra-aa-recovery-routes/1'],
    'peer_routes': ['avenra-peer-recovery-routes/1'],
    'issuer_runtime': ['avenra-synthetic-issuer-runtime/1'],
    'issuer_routes': ['avenra-synthetic-issuer-recovery/1'],
    'participant_profiles': [
        'avenra-xml-tls-participant/1/d7272c7dcaeb6fa024f181fa212d62dd3cd0d92b9292e368ade0edd8690cb50b',
        'avenra-xml-participant/1/415786f9ce820675e0f1bc92a3993a644a7b72b9cf9c730c3c4ecf7ffbbf96bb',
        'avenra-xml-tls-synthetic-bank/1/e0d25be8c531093b9a838a4d01768fe717e87fd3231bacd41af037ba3dd8d3b3',
    ],
}
