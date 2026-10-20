"""Read-only deposit statements from authoritative native accounting records."""
from datetime import datetime, timezone
from decimal import Decimal
import operator

from lxml import etree

from ..errors import require
from .contracts import stamp, timestamp

NAMESPACE = 'http://api.rebit.org.in/FISchema/deposit'


def amount(paise):
    require(type(paise) is int, 'InvalidFI', 'Native monetary values must be integer paise.')
    return str(Decimal(paise).scaleb(-2).quantize(Decimal('.01')))


def statement(gateway, account, detail, bounds, contracts):
    records = gateway.transactions(account['ledger'], all_pages=True)
    require(len(records) <= 5000, 'FIDataTooLarge', 'Native statement exceeds this declared response budget.', 413)
    events = [];balance = 0
    for record in sorted(records, key=lambda x: (x['timestamp'], int(x['id']))):
        change = 0
        for posting in record['postings']:
            require(posting['asset'] == 'INR/2' or account['address'] not in {posting['source'], posting['destination']}, 'InvalidFI', 'Account contains an unsupported native asset.')
            if posting['source'] == account['address']: change -= posting['amount']
            if posting['destination'] == account['address']: change += posting['amount']
        balance += change
        if change and timestamp(bounds['from']) <= timestamp(record['timestamp']) <= timestamp(bounds['to']): events.append((record, change, balance))
    volumes = next((x for x in gateway.accounts(account['ledger']) if x['address'] == account['address']), {})
    authoritative = volumes.get('volumes', {}).get('INR/2', {}).get('balance', 0)
    require(balance == authoritative, 'InvalidFI', 'Native journal and native account volume disagree; no statement is released.', 409)
    root = etree.Element('{'+NAMESPACE+'}Account', nsmap={None:NAMESPACE}, type='deposit', maskedAccNumber=account['masked'], version='2.0.0', linkedAccRef=account['link_ref'])
    def child(parent, name, **attributes): return etree.SubElement(parent, '{'+NAMESPACE+'}'+name, **{k:str(v) for k,v in attributes.items()})
    if 'PROFILE' in detail['consentTypes']:
        holders = child(child(root, 'Profile'), 'Holders', type='SINGLE')
        child(holders, 'Holder', name=account['holder'], dob='1990-01-01', mobile='9999999999', ckycRegistered='NO_DATA_AVAILABLE')
    if 'SUMMARY' in detail['consentTypes']:
        child(root, 'Summary', currentBalance=amount(authoritative), currency='INR', balanceDateTime=stamp(), accountType='REGULAR', accountSubType='SAVINGS', branch='Avenra synthetic branch', facility='NO_FACILITY_GRANTED', ifsc='AVEN0000001', micrCode='000000000', openingDate=account['opening_date'], status='ACTIVE')
    if 'TRANSACTIONS' in detail['consentTypes']:
        comparisons = {'=':operator.eq,'!=':operator.ne,'>':operator.gt,'<':operator.lt,'>=':operator.ge,'<=':operator.le}
        selected=[]
        for record, change, running in events:
            kind = 'CREDIT' if change > 0 else 'DEBIT'
            if not all(comparisons[f['operator']](kind, f['value']) if f['type']=='TRANSACTIONTYPE' else comparisons[f['operator']](Decimal(abs(change)).scaleb(-2), Decimal(f['value'])) for f in detail.get('DataFilter', [])): continue
            selected.append((record,change,running,kind))
        if selected:
            transactions = child(root, 'Transactions', startDate=bounds['from'][:10], endDate=bounds['to'][:10])
            for record,change,running,kind in selected:
                child(transactions, 'Transaction', type=kind, mode='OTHERS', amount=amount(abs(change)), transactionalBalance=amount(running), transactionTimestamp=record['timestamp'], valueDate=record['timestamp'], txnId=str(record['id']), narration='Synthetic native journal entry', reference=record.get('reference', ''))
    result = etree.tostring(root, encoding='UTF-8', xml_declaration=True)
    contracts.document(result);return result
