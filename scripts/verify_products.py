#!/usr/bin/env python3
"""Exercise native lending and savings through scoped, durable Avenra commands."""
import json
from decimal import Decimal
from pathlib import Path
import secrets
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.util import application_fingerprint


def main():
    credentials = json.loads((ROOT / '.local/data/credentials.json').read_text())
    tenant, key = credentials['tenant_id'], credentials['api_key']
    bindings = json.loads((ROOT / '.local/data/services.json').read_text())['services']['banking_core']['tenant_bindings']
    env = next(k.split('/', 1)[1] for k in bindings if k.startswith(tenant + '/'))
    base = 'http://127.0.0.1:4827/api/tenants/' + tenant + '/environments/' + env + '/banking'
    report = {'passed': False, 'environment_id': env, 'created_at': time.time(), 'checks': []}

    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(name + ': ' + str(detail))

    def api(path, body=None):
        req = Request(base + path, method='POST' if body is not None else 'GET', data=json.dumps(body).encode() if body is not None else None, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=15) as r:
                return json.load(r)
        except HTTPError as e:
            raise RuntimeError(str(e.code) + ' ' + e.read(1800).decode()) from e

    def command(action, body, identity=None):
        data = {'action': action, 'body': body, 'request_key': 'portfolio-' + secrets.token_hex(12)}
        if identity:
            data['resource_id'] = identity
        accepted = api('/core-commands', data)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            value = api('/core-commands/' + accepted['id'])
            if value['state'] in {'succeeded', 'rejected'}:
                break
            time.sleep(.2)
        check('Native ' + action + ' succeeds', value['state'] == 'succeeded', value.get('result_json') or value.get('error'))
        check('Replay ' + action + ' retains command identity', api('/core-commands', data)['id'] == accepted['id'])
        return json.loads(value['result_json'])

    today = time.strftime('%d %B %Y')
    dates = {'dateFormat': 'dd MMMM yyyy', 'locale': 'en'}
    suffix = secrets.token_hex(5)
    try:
        client = command('create_client', {**dates, 'officeId': 1, 'legalFormId': 1, 'firstname': 'Avenra', 'lastname': 'PortfolioTest', 'externalId': 'portfolio-' + suffix, 'active': True, 'activationDate': today})['clientId']
        product = command('create_loan_product', {**dates, 'name': 'Avenra Test Loan ' + suffix, 'shortName': suffix[:4], 'currencyCode': 'INR', 'digitsAfterDecimal': 2, 'inMultiplesOf': 0, 'principal': 10000, 'numberOfRepayments': 12, 'repaymentEvery': 1, 'repaymentFrequencyType': 2, 'interestRatePerPeriod': 1, 'interestRateFrequencyType': 2, 'amortizationType': 1, 'interestType': 0, 'interestCalculationPeriodType': 1, 'transactionProcessingStrategyCode': 'mifos-standard-strategy', 'accountingRule': 1, 'isInterestRecalculationEnabled': False, 'daysInYearType': 1, 'daysInMonthType': 1, 'loanScheduleType': 'CUMULATIVE', 'loanScheduleProcessingType': 'HORIZONTAL'})['resourceId']
        loan = command('apply_loan', {**dates, 'clientId': client, 'productId': product, 'principal': 10000, 'loanTermFrequency': 12, 'loanTermFrequencyType': 2, 'numberOfRepayments': 12, 'repaymentEvery': 1, 'repaymentFrequencyType': 2, 'interestRatePerPeriod': 1, 'amortizationType': 1, 'interestType': 0, 'interestCalculationPeriodType': 1, 'transactionProcessingStrategyCode': 'mifos-standard-strategy', 'expectedDisbursementDate': today, 'submittedOnDate': today, 'loanType': 'individual'})['loanId']
        command('approve_loan', {**dates, 'approvedOnDate': today}, loan)
        command('disburse_loan', {**dates, 'actualDisbursementDate': today}, loan)
        portfolio = api('/core/loans/' + str(loan))
        check('Disbursed loan has native principal outstanding and repayment schedule', portfolio['summary']['principalOutstanding'] == 10000 and len(portfolio['repaymentSchedule']['periods']) == 13)
        command('repay_loan', {**dates, 'transactionDate': today, 'transactionAmount': 1000}, loan)
        portfolio = api('/core/loans/' + str(loan))
        summary = portfolio['summary']
        paid = sum(Decimal(str(summary[x])) for x in ('principalPaid', 'interestPaid', 'feeChargesPaid', 'penaltyChargesPaid'))
        check('Repayment is allocated once between principal and interest', summary['totalRepayment'] == 1000 and paid == Decimal('1000') and Decimal(str(summary['principalOutstanding'])) + Decimal(str(summary['principalPaid'])) == Decimal('10000') and Decimal(str(summary['totalOutstanding'])) == Decimal(str(summary['totalExpectedRepayment'])) - Decimal('1000'), summary)
        saving_product = command('create_savings_product', {'locale': 'en', 'name': 'Avenra Test Savings ' + suffix, 'shortName': suffix[-4:], 'description': 'Synthetic acceptance account', 'currencyCode': 'INR', 'digitsAfterDecimal': 2, 'inMultiplesOf': 0, 'nominalAnnualInterestRate': 0, 'interestCompoundingPeriodType': 1, 'interestPostingPeriodType': 4, 'interestCalculationType': 1, 'interestCalculationDaysInYearType': 365, 'accountingRule': 1})['resourceId']
        savings = command('apply_savings', {**dates, 'clientId': client, 'productId': saving_product, 'submittedOnDate': today})['savingsId']
        command('approve_savings', {**dates, 'approvedOnDate': today}, savings)
        command('activate_savings', {**dates, 'activatedOnDate': today}, savings)
        command('deposit_savings', {**dates, 'transactionDate': today, 'transactionAmount': 2500, 'paymentTypeId': 1}, savings)
        command('withdraw_savings', {**dates, 'transactionDate': today, 'transactionAmount': 500, 'paymentTypeId': 1}, savings)
        native = api('/core/savings/' + str(savings))
        check('Native savings deposit and withdrawal conserve the expected balance', native['summary']['accountBalance'] == 2000, native['summary'])
        report.update(passed=True, client_id=client, loan_id=loan, savings_id=savings)
    except Exception as e:
        import traceback
        report.update(error=str(e), traceback=traceback.format_exc())
    report['application_fingerprint'] = application_fingerprint()
    path = ROOT / '.local/product-verification.json'
    attempts = ROOT / '.local/verification-attempts'
    attempts.mkdir(exist_ok=True)
    text = json.dumps(report, indent=2) + '\n'
    path.write_text(text)
    (attempts / ('products-' + str(time.time_ns()) + '.json')).write_text(text)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(path), 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
