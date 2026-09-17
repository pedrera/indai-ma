"""Document-scoped documentary comparison, with explicit provenance for extracted and derived values."""
import re
import unicodedata
from commercial_models import CommercialComparison, ContractComparisonFacts, ContractFact, CommercialSource, CommercialStatus
from contractual_analysis import extract_contractual_volume_terms

COMPARISON = re.compile(r'\b(?:compar\w*|diferencias?|frente a|versus|vs\.?)(?:\b|\s)', re.I)
FIELDS = {
    'flexibility': ('reference_volume_gwh', 'flexibility_percent', 'monthly_min_gwh', 'monthly_max_gwh'),
    'take_or_pay': ('annual_volume_gwh', 'take_or_pay_percent', 'minimum_annual_gwh'),
    'excess': ('excess_surcharge_eur_mwh',),
}
LABELS = {
    'reference_volume_gwh': ('Referencia mensual', 'GWh'), 'flexibility_percent': ('Flexibilidad', '%'),
    'monthly_min_gwh': ('Mínimo mensual flexible', 'GWh'), 'monthly_max_gwh': ('Máximo mensual flexible', 'GWh'),
    'annual_volume_gwh': ('Volumen anual', 'GWh'), 'take_or_pay_percent': ('Take-or-pay', '%'),
    'minimum_annual_gwh': ('Mínimo anual take-or-pay', 'GWh'), 'excess_surcharge_eur_mwh': ('Precio del exceso', 'EUR/MWh'),
}


def normalize(text):
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in unicodedata.normalize('NFKD',text.lower())
                                                         if not unicodedata.combining(c))).split())


def named_groups(request, matches):
    groups = {}
    for match in matches:
        groups.setdefault(match.chunk.document_id, []).append(match)
    selected = []
    query = normalize(request)
    for group in groups.values():
        tokens = [t for t in normalize(group[0].chunk.document_name.rsplit('.',1)[0]).split()
                  if t not in {'contrato','contract','de','del','suministro','gas','natural'} and not t.isdigit()]
        clients = [m[1].strip() for chunk in group for m in re.finditer(r'(?im)^Cliente:\s*(.+)$',chunk.chunk.text)]
        if (tokens and all(re.search(rf'\b{re.escape(t)}\b',query) for t in tokens)) or any(
                re.search(rf'\b{re.escape(normalize(client))}\b',query) for client in clients):
            selected.append(group)
    return selected


def build_comparison(request, groups, result, additional_facts):
    query = normalize(request)
    topics = [key for key, pattern in [('flexibility',r'flexib|rango|referencia'),
        ('take_or_pay',r'take.or.pay|minimo anual'), ('excess',r'exce|recargo')]
        if re.search(pattern,query)] or list(FIELDS)
    contracts = []
    for matches in groups:
        sources = [CommercialSource(**{k:v for k,v in m.source_dict().items() if k != 'chunk_id'}) for m in matches]
        client = next((m[1].strip() for chunk in matches for m in re.finditer(r'(?im)^Cliente:\s*(.+)$',chunk.chunk.text)),None)
        contract = ContractComparisonFacts(document_id=matches[0].chunk.document_id,
            document_name=matches[0].chunk.document_name, customer=client)
        # Existing extractor is called once per document; never on merged contracts.
        terms = extract_contractual_volume_terms([{'chunk_id':str(i),'text':m.chunk.text} for i,m in enumerate(matches)])
        for name in ('reference_volume_gwh','flexibility_percent','excess_surcharge_eur_mwh'):
            value = getattr(terms,name)
            if value is not None:
                index = int(terms.field_sources[name])
                contract.facts.append(ContractFact(name=name,value=value,unit=LABELS[name][1],
                    evidence=matches[index].chunk.text,source=sources[index]))
        # Existing annual/take-or-pay extraction, independently per document.
        from commercial_models import CommercialAgentResult
        scratch = CommercialAgentResult(status=CommercialStatus.COMPLETED,summary='')
        additional_facts(matches,sources,scratch)
        contract.facts += [f for f in scratch.contract_facts if f.name in {'annual_volume_gwh','take_or_pay_percent'}]
        numeric = r'(\d+(?:[.,]\d+)?)'
        patterns = {
            'monthly_min_gwh': rf'entre\s*:?\s*{numeric}\s*GWh\s+y\s+\d+(?:[.,]\d+)?\s*GWh',
            'monthly_max_gwh': rf'entre\s*:?\s*\d+(?:[.,]\d+)?\s*GWh\s+y\s+{numeric}\s*GWh',
            'minimum_annual_gwh': rf'(?:m[ií]nimo(?:\s+(?:anual|el equivalente a|de))?\s+|obligaci[oó]n\s+m[ií]nima\s+(?:anual\s+)?(?:de\s+take-or-pay\s+)?(?:ser[aá]\s+de\s*)?:?\s*){numeric}\s*GWh',
        }
        boundary_patterns = {
            'monthly_min_gwh': rf'(?:l[ií]mite inferior(?:\s+de flexibilidad)?\s*(?:de|:)?|consumo mensual sea inferior a)\s*{numeric}\s*GWh',
            'monthly_max_gwh': rf'l[ií]mite superior(?:\s+de flexibilidad)?\s*(?:de|:)?\s*{numeric}\s*GWh',
        }
        conflicted = set()
        for name, pattern in patterns.items():
            candidates = [(float(m[1].replace(',','.')),i,m[0]) for i,chunk in enumerate(matches)
                          for candidate_pattern in (pattern, boundary_patterns.get(name, r"(?!x)x"))
                          for m in re.finditer(candidate_pattern,chunk.chunk.text,re.I)]
            if len({v for v,_,_ in candidates}) > 1:
                conflicted.add(name)
            if len({v for v,_,_ in candidates}) == 1:
                value,index,quote = candidates[0]
                contract.facts.append(ContractFact(name=name,value=value,unit='GWh',evidence=quote,source=sources[index]))
        contract.facts = list({f.name:f for f in contract.facts}.values())
        facts = {f.name: f for f in contract.facts}
        derivations = {
            'monthly_min_gwh': ('reference_volume_gwh', 'flexibility_percent', lambda a,b: a*(1-b/100)),
            'monthly_max_gwh': ('reference_volume_gwh', 'flexibility_percent', lambda a,b: a*(1+b/100)),
            'minimum_annual_gwh': ('annual_volume_gwh', 'take_or_pay_percent', lambda a,b: a*b/100),
        }
        for name, (left, right, calculate) in derivations.items():
            if name not in facts and name not in conflicted and left in facts and right in facts:
                a, b = facts[left], facts[right]
                contract.facts.append(ContractFact(name=name, value=round(calculate(a.value,b.value),12),
                    unit='GWh', origin='deterministic_calculation', input_facts=[left,right],
                    input_sources=[a.source,b.source], source=a.source,
                    evidence=f'Cálculo determinista a partir de {LABELS[left][0]}={a.value} y {LABELS[right][0]}={b.value}.'))
        contracts.append(contract)
        result.sources.extend(sources)
        available = {f.name for f in contract.facts}
        for topic in topics:
            for name in FIELDS[topic]:
                if name not in available:
                    result.warnings.append(f'{client or contract.document_name}: {LABELS[name][0]} no encontrado en la evidencia recuperada.')
    result.comparison = CommercialComparison(compared_topics=topics,contracts=contracts)
    result.summary = 'Comparación documental de las condiciones contractuales recuperadas.'
    result.status = CommercialStatus.PARTIAL if result.warnings else CommercialStatus.COMPLETED
    return result


def comparison_rows(result):
    comparison = result.comparison
    rows = []
    for key in dict.fromkeys(k for topic in comparison.compared_topics for k in FIELDS[topic]):
        label,unit = LABELS[key]
        row = {'Condición':label}
        for contract in comparison.contracts:
            title = contract.customer or contract.document_name
            # Document name disambiguates repeated customer names (e.g. contract versions).
            if sum(c.customer == contract.customer for c in comparison.contracts) > 1:
                title = contract.document_name
            fact = next((f for f in contract.facts if f.name == key),None)
            value = f'{fact.value:g}' if fact and isinstance(fact.value,(int,float)) else str(fact.value) if fact else None
            row[title] = ('No encontrado en la evidencia recuperada.' if fact is None else
                          f'Spot + {value} {unit}' if key == 'excess_surcharge_eur_mwh' else
                          f'±{value}{unit}' if key == 'flexibility_percent' else f'{value} {unit}')
            if fact and fact.origin == 'deterministic_calculation':
                row[title] += ' (calculado)'
        rows.append(row)
    return rows


def comparison_text(result):
    lines = [result.summary]
    for row in comparison_rows(result):
        lines.append(row['Condición'] + ': ' + '; '.join(f'{k}: {v}' for k,v in row.items() if k != 'Condición'))
    lines += ['Fuentes:', *dict.fromkeys(f.source.label for c in result.comparison.contracts for f in c.facts)]
    lines += ['Aviso: '+w for w in result.warnings]
    return '\n\n'.join(lines)
