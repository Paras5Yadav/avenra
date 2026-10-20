"""Consent bounds and calendar arithmetic for the declared deposit profile."""
import calendar
from datetime import datetime, timezone
import math

from ..errors import require
from .contracts import timestamp


def period(value, field):
    require(isinstance(value, dict), 'InvalidConsentDetail', 'Use a structured ' + field + '.')
    number = value.get('value')
    require(type(number) in {int, float} and math.isfinite(number) and number == int(number), 'InvalidConsentDetail', 'Use a finite integral ' + field + '.')
    require(0 < value['value'] <= 10000 and value.get('unit') in ({'HOUR', 'DAY', 'MONTH', 'YEAR'} if field == 'frequency' else {'DAY', 'MONTH', 'YEAR', 'INF'}), 'InvalidConsentDetail', 'Unsupported ' + field + ' bounds.')


def add_months(start, months):
    original = datetime.fromtimestamp(start, timezone.utc);index = original.year * 12 + original.month - 1 + months
    return original.replace(year=index // 12, month=index % 12 + 1, day=min(original.day, calendar.monthrange(index // 12, index % 12 + 1)[1])).timestamp()


def window(start, current, unit):
    if unit in {'HOUR', 'DAY'}: return max(0, int((current - start) // (3600 if unit == 'HOUR' else 86400)))
    a, b = datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(current, timezone.utc)
    months = (b.year - a.year) * 12 + b.month - a.month
    if add_months(start, months) > current: months -= 1
    return max(0, months // 12 if unit == 'YEAR' else months)


def valid_detail(detail):
    require(detail.get('fiTypes') == ['DEPOSIT'] and detail.get('consentTypes') and len(detail['consentTypes']) == len(set(detail['consentTypes'])), 'InvalidConsentDetail', 'This selected wire profile supports deposit information and unique consent sections.')
    require(timestamp(detail['consentExpiry']) > timestamp(detail['consentStart']), 'InvalidConsentDetail', 'Consent expiry must follow its start.')
    bounds = detail['FIDataRange'];require(timestamp(bounds['to']) >= timestamp(bounds['from']), 'InvalidDateRange', 'Invalid consent financial-data range.')
    period(detail['Frequency'], 'frequency');period(detail['DataLife'], 'data life')
    require(len(detail.get('DataFilter', [])) <= 10, 'InvalidConsentDetail', 'Filter budget exceeded.')
    for rule in detail.get('DataFilter', []):
        if rule['type'] == 'TRANSACTIONTYPE': require(rule['operator'] in {'=', '!='} and rule['value'] in {'CREDIT', 'DEBIT'}, 'InvalidConsentDetail', 'Unsupported transaction type filter.')
        else:
            from decimal import Decimal, InvalidOperation
            try: value = Decimal(rule['value'])
            except (InvalidOperation, ValueError): value = Decimal('NaN')
            require(value.is_finite() and value >= 0, 'InvalidConsentDetail', 'Use a finite nonnegative amount filter.')


def active(state, detail, current):
    require(state != 'REVOKED', 'ConsentRevoked', 'Consent has been revoked.', 403)
    require(state != 'PAUSED', 'ConsentPaused', 'Consent is paused.', 403)
    require(state != 'EXPIRED','ConsentExpired','Consent has expired.',403)
    require(state == 'ACTIVE', 'InvalidConsentId', 'Consent is not active.')
    require(current < timestamp(detail['consentExpiry']), 'ConsentExpired', 'Consent has expired.', 403)
    require(current >= timestamp(detail['consentStart']), 'InvalidRequest', 'Consent has not started.')


def data_range(detail, bounds, current):
    start, end = timestamp(bounds['from']), timestamp(bounds['to'])
    require(timestamp(detail['FIDataRange']['from']) <= start <= end <= timestamp(detail['FIDataRange']['to']) and end <= current + 1, 'InvalidDateRange', 'Requested range exceeds the consent or current time.')


def expiry(detail, current):
    life = detail['DataLife'];value = int(life['value'])
    if life['unit'] == 'INF': return None
    return current + value * 86400 if life['unit'] == 'DAY' else add_months(current, value * (12 if life['unit'] == 'YEAR' else 1))
