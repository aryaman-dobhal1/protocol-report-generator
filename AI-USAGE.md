# AI tools used and assistance received

## AI coding tools
- **Claude (Anthropic)** was used as a coding assistant to generate and iterate on the code: the React frontend, the FastAPI backend, the sample inputs, the tests and the documentation drafts. Prompts followed the assignment brief and my feedback on the design.
- I reviewed the generated code, ran the three demo cases, and can explain each module (`engine.py`, `narrative.py`, `builders.py`, `main.py`, `App.jsx`).

## AI inside the product
- **Groq** (model set by `GROQ_MODEL`, default `llama-3.3-70b-versatile`) is used only for the "Summary and observations" draft, and only when `GROQ_API_KEY` is set and AI is requested.
- It receives verified facts (counts, limits, failing and missing samples) and the retrieved reference passages. It never receives the raw files and never calculates results.
- Its output is validated in `narrative.py` (`_grounded`) and replaced by the rule-based summary if it cites an unknown source, omits a failure or gap, contains an unverified number or uses causal wording.
- Pass/Fail, counts and acceptance limits are computed by plain Python in `engine.py`. AI cannot change them.

## Assistance from other developers
- **Ishan** helped me understand the project and the assignment, and helped me work out how the report template should look (its four sections, the fixed wording and the editable placeholders).
- All code in this submission was generated with the AI tools above and reviewed by me. I can explain the design decisions and code paths independently.

## Verification
- Ran `python tests/test_cases.py`: all three demo cases (complete, failing, incomplete) pass. The test opens the generated DOCX, XLSX and combined PDF and checks them for consistency.
- The Groq grounding check was tested with mocked model responses (one good draft accepted, five bad drafts rejected). It has not been run against the live API in testing.