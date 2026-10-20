"""Gate the existing coordinator on an independently authenticated inbox receipt."""
import json

from .errors import DomainError,require
from .gateways import EngineError
from .participant_peer_contract import ADAPTER
from .upi_wire import XmlParticipantAdapter,authenticate,encode
from .util import digest,now


class PeerParticipantAdapter:
    def deliver(self,upi,tenant,env,payment,message,token):
        from .upi import ROUTES
        role=ROUTES[message['step']][2]
        binding,connection=upi.connections._pinned(tenant,env,payment,role,message['recipient'])
        require(binding['adapter_id']==ADAPTER and connection,'CONNECTION_CONFLICT','Independent participant delivery needs its original pinned connection.',409)
        configuration=json.loads(connection['config_json'])
        client=upi.connections._peer_client(tenant,env,message['recipient'],configuration)
        raw=encode(json.loads(message['document_json']),message['signature'])
        with upi.store.reader() as db:
            current=upi.get(db,'upi_payments',tenant,env,payment['id'])
            require(current['lease_token']==token,'PAYMENT_BUSY','Participant delivery lease changed.',409)
            key=db.execute('SELECT public_key FROM upi_keys WHERE id=? AND participant_id=?',(message['key_id'],message['sender'])).fetchone()
            require(key,'UPI_WIRE_SIGNATURE','Original scoped verification key is unavailable.',403)
            document,signature=authenticate(raw,key['public_key']);upi._verify(db,current,document,signature)
            clock=upi.clock(tenant,env,db)
        try:
            receipt=client.deliver(raw,clock)
        except (EngineError,DomainError) as error:
            # A missing, malformed or refused acknowledgement is not permission
            # to run the logical recipient locally, decline money, or choose a
            # different participant/financial identity.
            with upi.store.transaction() as db:
                require(db.execute('SELECT 1 FROM upi_payments WHERE id=? AND lease_token=?',(payment['id'],token)).fetchone(),
                        'PAYMENT_BUSY','Participant delivery lease changed.',409)
                db.execute('UPDATE upi_deliveries SET available_at=?,error=?,updated_at=? WHERE message_id=?',
                           (upi.clock(tenant,env,db)+1,'PEER_'+error.code,now(),message['id']))
                upi._event(db,payment,message['id'],'peer.unconfirmed',{'connection_id':connection['id'],'adapter_id':ADAPTER,
                           'error_code':error.code,'financial_dispatch':False,'original_message_id':message['id']})
            return False
        with upi.store.transaction() as db:
            require(db.execute('SELECT 1 FROM upi_payments WHERE id=? AND lease_token=?',(payment['id'],token)).fetchone(),
                    'PAYMENT_BUSY','Participant delivery lease changed.',409)
            existing=db.execute("SELECT details_json FROM upi_events WHERE message_id=? AND event='peer.accepted'",(message['id'],)).fetchone()
            if existing:
                require(json.loads(existing['details_json'])['receipt_hash']==digest(receipt),'PEER_RECEIPT_CONFLICT',
                        'Independent participant changed its original durable acknowledgement.',409)
            else:
                upi._event(db,payment,message['id'],'peer.accepted',{'connection_id':connection['id'],'adapter_id':ADAPTER,
                           'credential_binding':configuration['credential_binding'],'connection_config_hash':binding['config_hash'],'receipt_hash':digest(receipt),'receipt':receipt,
                           'transport':client.receipts[-1],'server_signer_public':client.document['server_signer_public'],
                           'client_signer_public':client.document['client_signer_public'],'financial_confirmation':False})
        # Preserve the existing coordinator/accounting behaviour. Independent
        # receivers own their inboxes; this app retains a local orchestration
        # receipt only after authenticating that receiver's durable acceptance.
        return XmlParticipantAdapter().deliver(upi,tenant,env,payment,message,token)
