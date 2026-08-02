from decimal import Decimal

from workflow_engine.normalizers import normalize_number, normalize_term, parse_rate_expression


def test_normalize_numbers():
    assert normalize_number('12,5%')==Decimal('12.5')
    assert normalize_number('до 13%')==Decimal('13')
    assert normalize_number('1 234,56')==Decimal('1234.56')

def test_normalize_terms():
    assert normalize_term('3 месяца')['term_min_days']==90
    assert normalize_term('31–60 дней')['term_max_days']==60
    assert normalize_term('свыше 365 дней')['term_min_days']==365

def test_rate_formula():
    x=parse_rate_expression('СР + 1,25 п.п.')
    assert x['rate_type']=='BENCHMARK_SPREAD' and x['spread_pp']==1.25
    assert parse_rate_expression('индивидуальная ставка')['rate_type']=='INDIVIDUAL'
