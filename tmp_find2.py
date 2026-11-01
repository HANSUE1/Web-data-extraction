import re
s = open('countries.html', encoding='utf-8').read()
scripts = re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL)
for i, sc in enumerate(scripts):
    if 'country' in sc.lower() or '[' in sc:
        print(f'===SCRIPT{i}===')
        print(sc[:400])
        print()

