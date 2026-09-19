# indAI MA Commercial MVP demo guide

This guide is a 5–10 minute demonstration of the deterministic Commercial MVP.
It uses the fictitious contracts in [`demo/contracts`](../demo/contracts).

## Primary flow: Hospital Costa Sur

1. Start LM Studio with the embedding model configured in `.env`, then start:
   `streamlit run app.py`.
2. In **Documentación RAG**, index both files from `demo/contracts/`.
3. Return to **Business** and select **Hospital Costa Sur**.
4. Show the complete natural-language question and press **Analizar**.
5. Explain that Supervisor routes the question automatically to CommercialAgent,
   ProcurementAgent and RiskAgent.
6. Start with the **Executive Summary** and **Key Metrics**:
   - contractual excess: `0.2 GWh`;
   - procurement SHORT: `0.5 GWh`;
   - spot exposure: `21,000 EUR`.
7. Show **Evidence** for the contract and **Provenance** for documentary facts,
   operating inputs and calculated values.
8. Explain that contractual excess and Procurement SHORT measure different
   things and must not be added or substituted.
9. Open **Technical Details** / Pipeline Inspector to show deterministic routing,
   RAG retrieval, tool calls and zero generation-LLM calls.

## Optional follow-up demos

- **Comparar contratos:** demonstrates document-isolated Hospital Costa Sur and
  Industrias Mediterráneo extraction.
- **Aprovisionamiento:** demonstrates the 120 GWh / 95 GWh / 42 EUR/MWh SHORT
  and spot coverage calculation.
- **Escenario de riesgo:** demonstrates the explicit +10% demand stress scenario.

Each example fills the editable question. Pressing the example does not execute;
the user reviews or edits the question and presses **Analizar**.

## What to emphasize

Lead with the business result and evidence. Use the Inspector afterward to show
how routing, RAG, deterministic tools and operation diagnostics support the answer.
Do not claim production integrations, enterprise security or autonomous trading
capabilities.
