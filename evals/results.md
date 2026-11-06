# Eval results

Append-only log written by `uv run python -m evals.run_evals --record`. Newest last. Each run ends with a machine-readable counts comment that the next run reads for its Delta column.

## 2026-11-06 | 351a2c7 | claude-haiku-5-5 | effort low | prompt 9695c4cb | split all | scored 32 of 32

Labels: verified, dated 2026-11-05. Judge model claude-sonnet-5-5 (judge uncalibrated). Eval database data/eval.db.

| Metric | Result | Delta | Gate |
|---|---|---|---|
| Profiles submitted | 29/32 (90.6%) | n/a | report |
| Infrastructure errors | 0/32 (0.0%) | n/a | report |
| Repair rate | 24/32 (75.0%) | n/a | report |
| Category accuracy | 24/29 (82.8%) | n/a | >= 85% FAIL |
| Stage accuracy | 21/29 (72.4%) | n/a | report |
| Funding exact | 11/18 (61.1%) | n/a | report |
| Funding within 10% | 12/18 (66.7%) | n/a | report |
| Unexpected disclosure | 2/11 (18.2%) | n/a | report |
| Fabricated funding | 0/70 (0.0%) | n/a | <= 3% pass |
| Unverifiable funding | 69/70 (98.6%) | n/a | report |
| Source ledger membership | 260/260 (100.0%) | n/a | report |
| Thin Other profiles | 3/29 (10.3%) | n/a | report |
| Judge business_model correct | 3/25 (12.0%); partial 12, incorrect 7, unjudged 0, missing 3 [uncalibrated] | n/a | report |
| Judge target_user correct | 6/26 (23.1%); partial 16, incorrect 1, unjudged 0, missing 3 [uncalibrated] | n/a | report |
| Cost per company | mean USD 0.0231, max USD 0.0272 | n/a | report |
| Total cost | USD 0.8333 (enrichment 0.7389, judge 0.0944) | n/a | report |

Gates: FAIL (category_accuracy).

### Misses
- Synthesia (10933652, Companies House: SYNTHESIA LIMITED)
  - not submitted (stop=invalid, failure=invalid_output)
- Tractable (09315523, Companies House: TRACTABLE LTD.)
  - category: golden Fintech and payments; observed Enterprise software and cloud
- Quantexa (10045407, Companies House: QUANTEXA LIMITED)
  - stage: golden Series F; observed None
  - funding: golden US$175m; observed unknown (off)
- Peak AI (09307701, Companies House: PEAK AI LIMITED)
  - category: golden Enterprise software and cloud or E-commerce and retail tech or Logistics and supply chain; observed Data, AI infrastructure and developer tools
- Builder.ai (10998363, Companies House: ENGINEER.AI GLOBAL LIMITED)
  - category: golden Enterprise software and cloud; observed Consultancy and services
  - stage: golden Series D; observed None
  - funding: golden US$250m; observed US$30m (off)
- Darktrace (13264637, Companies House: DARKTRACE LIMITED)
  - unexpected disclosure: golden has no round; observed ['US$50m', 'US$75m', 'US$18m']
- BenevolentAI (09781806, Companies House: BENEVOLENTAI LIMITED)
  - unexpected disclosure: golden has no round; observed ['US$149m']
- Exscientia (13483814, Companies House: EXSCIENTIA LIMITED)
  - stage: golden Series D; observed None
- Babylon Health (09229684, Companies House: BABYLON HEALTHCARE SERVICES LIMITED)
  - stage: golden Series C; observed None
  - funding: golden US$550m; observed unknown (off)
- Satellite Vu (10163800, Companies House: GLOBAL SATELLITE VU LTD)
  - funding: golden US$41m; observed US$16m (off)
- Graphcore (10185006, Companies House: GRAPHCORE LIMITED)
  - stage: golden Growth; observed Series E
  - funding: golden US$450m; observed US$222m (off)
- Improbable (08070525, Companies House: IMPROBABLE WORLDS LIMITED)
  - category: golden Consumer social, media and gaming; observed Consultancy and services
  - stage: golden Growth; observed None
  - funding: golden US$112m; observed US$100m (off)
- Riverlane (10260255, Companies House: RIVERLANE LTD)
  - not submitted (stop=invalid, failure=invalid_output)
- Faculty (08873131, Companies House: FACULTY SCIENCE LIMITED)
  - stage: golden Growth; observed Series B
  - funding: golden US$43m; observed US$39m (within 10%)
- Adarga (09742902, Companies House: ADARGA LIMITED)
  - category: golden Enterprise software and cloud or Defense, aerospace and space; observed Data, AI infrastructure and developer tools
  - stage: golden Series B; observed None
- Oxford Centre for Artificial Intelligence (13617274, Companies House: OXFORD CENTRE FOR ARTIFICIAL INTELLIGENCE)
  - not submitted (stop=invalid, failure=invalid_output)
- Judge:
  - PolyAI business_model: incorrect (The candidate says there is no published standard pricing and only quote-based plans. The reference says there is published per-minute pricing (2 cents per connected minute plus text tokens) alongside custom quotes for larger deployments, so the two conflict.)
  - Tractable business_model: partial (The candidate correctly says the software is licensed to enterprise customers. It leaves out the pricing structure (per-claim or annual fees, volume-based, negotiated, not published) and adds product detail instead.)
  - Speechmatics business_model: partial (The candidate correctly gives usage-based pricing and enterprise on-prem/on-device options. It leaves out prepaid credits, the per-second and per-character metering, the $100 free credit and TTS. It also mentions only speech-to-text.)
  - Cleo AI business_model: partial (The candidate correctly names subscription tiers and express fees on cash advances. It omits the specific prices and fee range, and it doesn't say advances are available without a subscription. It also adds interchange and marketplace commissions, which the reference doesn't mention.)
  - Quantexa business_model: partial (The candidate describes a software platform and products, which is consistent with selling software. It omits the licence-based revenue model and the growth from new customers plus licence expansion, so it is vaguer than the reference.)
  - Peak AI business_model: partial (The candidate is broadly consistent but vague. It omits the subscription model, the three editions, the usage-based scaling, and the demo-led sales with unpublished pricing.)
  - Matillion business_model: incorrect (The candidate describes the product (a cloud ETL/ELT platform) rather than the business model. It says nothing about consumption-based credits, annual packages or the Developer/Teams/Scale plans.)
  - Builder.ai business_model: incorrect (The candidate describes what the company sells and its brands. The reference describes pricing: no published price list, cost driven by features, OS and timeline, and weekly or monthly payments. The two describe different things and share no pricing details.)
  - BenevolentAI business_model: partial (The candidate correctly names pharma collaborations as a revenue route, but it omits the AstraZeneca and Merck KGaA deals, the milestone and royalty terms, and the revenue figures. It also adds own-pipeline revenue, which the reference does not mention.)
  - Exscientia business_model: partial (The candidate correctly describes pharma partnership and licensing revenue, but it omits the milestone and royalty payment structure, the named partners, and the Recursion acquisition. It also adds in-house drug development, which the reference doesn't mention.)
  - Babylon Health business_model: incorrect (The candidate describes a telehealth practice that contracts with providers, including an NHS GP service. The reference describes private subscriptions, pay-as-you-go and insurer sales (e.g. Bupa), and says NHS contract terms are unconfirmed. The candidate asserts an NHS contract and omits the revenue model, so it contradicts and misses the reference.)
  - Huma business_model: partial (The candidate matches the reference on a B2B digital health cloud platform with pharma customers. It adds specifics (no-code, licensing, hospitals and governments) that the reference doesn't confirm, and it omits the reference's "not confirmed" caveat and the point about building apps.)
  - Satellite Vu business_model: incorrect (The candidate describes thermal imagery with early-access purchase commitments. The reference describes per-image catalogue pricing shown before ordering, plus tasking and sales-led ordering, so the two describe different models.)
  - Graphcore business_model: partial (The candidate matches the reference on IPU products and SoftBank ownership. It omits the sales channel (authorised cloud providers, pricing on request) and instead says Graphcore sells processors and software directly.)
  - Wayve Technologies business_model: partial (The candidate correctly says the AI Driver is licensed to automakers and not run as a robotaxi service. It leaves out the Uber arrangement, where Uber owns and operates the fleet. It also adds "fleet operators" as licensees, which the reference doesn't mention.)
  - Improbable business_model: incorrect (The candidate describes a venture builder for AI, Web3 and the metaverse. The reference describes a former defence arm earning government contract revenue and a metaverse business that builds virtual worlds, so the candidate describes something different and omits the defence/government revenue.)
  - Lyst business_model: partial (The candidate describes an aggregator platform but never mentions that Lyst earns commission on sales or that commission rate affects ranking, which are the core of the reference.)
  - Ocado Technology business_model: incorrect (The reference describes a fee-based model (recurring ~5% of partner retail sales plus ~4% upfront), but the candidate describes selling automation hardware and software through a division, which is a different model and omits the fee structure.)
  - Astroscale UK business_model: partial (The candidate matches the per-mission servicing for debris removal and life extension, and it names government and operator clients. It leaves out the named agencies (UKSA, ESA, JAXA) and the note that the fee figures are estimates. It also adds satellite operators as clients, where the reference says clients are primarily government.)
  - PolyAI target_user: partial (The candidate correctly says large enterprises running customer service operations. It leaves out contact centre teams as the specific users, and it adds an industry list the reference doesn't have.)
  - Tractable target_user: partial (The candidate includes insurers but adds dealerships, repairers, recyclers and fleet operators. It omits the reference's focus on insurers assessing vehicle and property damage, so it is broader and less specific.)
  - Speechmatics target_user: partial (The candidate is consistent with the reference but covers only some of its verticals (contact centres, healthcare, live media). It omits legal transcription, speech analytics, meeting assistants and EdTech, and it is vaguer overall.)
  - Cleo AI target_user: partial (The candidate is consistent with the reference's "consumers" but narrows it to young adults and Gen Z mainly in the US. The reference gives no such restriction, so the candidate adds specifics it doesn't support.)
  - Quantexa target_user: partial (The candidate's list of sectors fits "public and private sector organisations", but it omits the decision intelligence and financial crime focus and is only a rough match.)
  - Peak AI target_user: partial (The candidate matches retail and manufacturing. It swaps supply chain for consumer goods and gives only vague "commercial decisions" instead of pricing and inventory decisions.)
  - Builder.ai target_user: incorrect (The reference describes businesses and founders building apps themselves with little or no code, while the candidate describes businesses commissioning custom apps built by software teams, which is a different user and use case.)
  - Darktrace target_user: partial (The candidate names organisations that want cybersecurity protection, which is broadly consistent with the reference. It is vaguer, though. It leaves out the enterprise focus and the security teams as the actual users, and it adds an "AI-powered" qualifier that the reference doesn't have.)
  - Exscientia target_user: partial (The candidate correctly names pharma companies as partners. It adds biotech and licensees, but it omits the reference's R&D teams as users and its focus on large pharma, so it is vaguer.)
  - Babylon Health target_user: partial (The candidate covers NHS patients but narrows to online-first primary care such as GP at Hand and leaves out private GP service users.)
  - Satellite Vu target_user: partial (The candidate covers governments and national security, but it omits the defence and commercial earth observation users. It also adds economic monitoring and climate resilience, which the reference doesn't mention, and it doesn't say "allied".)
  - Wayve Technologies target_user: partial (The candidate correctly names automakers/OEMs but says the vaguer "fleet operators" instead of robotaxi operators, and it leaves out the licensing detail.)
  - Improbable target_user: partial (The candidate covers metaverse and gaming, which matches the reference. It describes the users as early-stage founders in AI and Web3 rather than game studios and entertainment platform builders, so it is vaguer and shifts the focus.)
  - Ocado Technology target_user: partial (The candidate includes grocery retailers, which matches the reference. It adds non-grocery segments the reference doesn't mention, and it leaves out the example customers (Kroger, Coles). The extra segments broaden the target user beyond the reference, but they don't clearly contradict it.)
  - Adarga target_user: partial (The candidate covers defence and security, which matches the reference. It leaves out intelligence customers and adds broad commercial and government clients, so it is vaguer and wider than the reference.)
  - Astroscale UK target_user: partial (The candidate matches the reference on satellite operators and space agencies and on UKSA. It leaves out ESA and Eutelsat OneWeb, and it adds US Space Force, which the reference doesn't list. That makes the examples partly different, though the core user categories agree.)
  - China Britain Artificial Intelligence Association target_user: partial (The candidate narrows the users to UK-based Chinese AI professionals, while the reference says UK-based AI professionals and researchers in the context of UK-China collaboration, so the candidate may exclude non-Chinese members. It also adds industry and academic contacts that the reference doesn't mention.)

<!-- sprout-eval-counts {"split": "all", "full": true, "scored": 32, "profiles_submitted": [29, 32], "infrastructure_errors": [0, 32], "repair_rate": [24, 32], "category_accuracy": [24, 29], "stage_accuracy": [21, 29], "funding_exact": [11, 18], "funding_within_10pct": [12, 18], "unexpected_disclosure": [2, 11], "fabricated_funding": [0, 70], "unverifiable_funding": [69, 70], "source_ledger": [260, 260], "thin_other": [3, 29], "ledger_unverifiable": 0, "cost_mean_usd": "0.0230909375", "cost_max_usd": "0.027178", "judge_business_model": [3, 25], "judge_target_user": [6, 26]} -->
