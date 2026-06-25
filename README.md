# FactoryBrain — Industrial Knowledge Management System

Captures the expertise of factory workers and supervisors so it isn't lost when
they leave or retire. Built with **Flask + SQLite + OpenAI GPT-4o**.

It turns a free-text experience into a fixed structured record
(equipment, problem type, cause, solution steps, severity, keywords),
stores it in a searchable database, and lets new employees ask questions
and receive answers built from the recorded fixes.

---

## How to run

```bash
# 1. (optional) create a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. add your OpenAI key (for real GPT-4o answers)
export OPENAI_API_KEY="sk-..."   # Windows: set OPENAI_API_KEY=sk-...
#   (or paste it into config.py)

# 4. run
python app.py
```

Open **http://127.0.0.1:5000**

> Without a key the app still runs in **Demo Mode** (rule-based extraction
> and answers) so you can present it without any cost. Add the key to enable
> real GPT-4o.

---

## Accounts

| Role  | How to access                                  |
|-------|------------------------------------------------|
| User  | Sign up from the login page (Create an account)|
| Admin | `admin@gmail.com` / `admin123` (seeded auto)   |

There are **two separate page systems**: one for employees (users) and one for
the admin, each with its own navigation.

---

## Features

- **Add Knowledge** — write the experience freely in Arabic or English; AI
  structures it into the same fixed format every time, which you can edit
  before saving.
- **Ask AI** — ask about a problem and get an answer built only from the
  recorded knowledge, with source entries.
- **Knowledge Base** — searchable cards by equipment, problem, cause, keyword.
- **Equipment Pages** — each machine with its past faults and solutions.
- **Incident Log** — record problems, downtime, and the solution used; open/close.
- **Rating System** — rate solutions 1–5 to surface the best ones.
- **Daily Tasks** — admin assigns daily tasks (inspections, training, operation
  steps, follow-ups) to new employees; employees check them off; admin tracks.
- **Dashboards** — user dashboard + admin dashboard (counts, most problematic
  equipment, best solutions, severity distribution, open/closed incidents).

---

## Project structure

```
factorybrain/
├── app.py              # Flask app: routes, DB, auth
├── ai_engine.py        # GPT-4o extraction + Q&A (with demo fallback)
├── config.py           # admin account, API key, model
├── requirements.txt
├── static/
│   ├── css/style.css   # FactoryBrain brand theme (Poppins + brand palette)
│   └── js/app.js
└── templates/          # all pages (user + admin)
```

The SQLite database `factorybrain.db` is created automatically on first run.
