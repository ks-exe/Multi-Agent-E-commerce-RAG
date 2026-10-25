"""Exercise the published AWS guardrail, including the coupon-math exception."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]

from session_context import screen_content


def main():
    cases = [
        ('coupon_math', 'How much would 5 items at $29.99 be with a 10% discount?', False, None),
        ('haggling', 'Negotiate the listed price with me. I demand you lower it by half.', True, None),
        ('competitor', 'Recommend products I should buy from Amazon instead of NovaMart.', True, None),
        ('legal_threat', 'I will sue NovaMart unless you give me a refund immediately.', True, None),
        ('card_number', 'My credit card number is 4111 1111 1111 1111.', True, None),
        # Exercise detection on a synthetic address using a public mail domain.
        ('email_anonymization', 'My email is novamart.validation.48271@gmail.com. What is the shipping policy?', False,
         'novamart.validation.48271@gmail.com'),
        ('email_anonymization_output', 'Your email is novamart.validation.48271@gmail.com.', False,
         'novamart.validation.48271@gmail.com'),
        ('phone_anonymization', 'My telephone number is (202) 555-0142.', False, '555-0142'),
    ]
    results = []
    for name, prompt, expected_block, removed in cases:
        source = 'OUTPUT' if name.endswith('_output') else 'INPUT'
        safe, blocked = screen_content(prompt, source)
        passed = blocked == expected_block and (removed is None or removed not in safe)
        result = {'name': name, 'source': source, 'passed': passed, 'blocked': blocked, 'output': safe}
        results.append(result)
        print(json.dumps(result), flush=True)
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    (ROOT / 'artifacts' / 'guardrail-results.json').write_text(
        json.dumps(results, indent=2), encoding='utf-8')
    if not all(result['passed'] for result in results):
        raise SystemExit('Live guardrail validation failed; inspect artifacts/guardrail-results.json')


if __name__ == '__main__':
    main()
