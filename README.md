# Astra

Get your standard client agreement signed on every job, without the manual back-and-forth. Upload the agreement once, mark where each party fills in and signs, then send it to every new client in a couple of clicks.

## Try it in 5 minutes

You need the company login from the submission form and any agreement as a PDF or Word (DOCX) file, up to 10 MiB and 50 pages.

> **Demo mode:** no emails are sent. When you send an agreement, a pop-up shows the client's signing link and access code (normally they would be emailed), and you open them yourself to act as the client. The link and code work for 24 hours and can be reused in that time.

### As the company

1. **Log in** at `/company/login/` with the company email and password.
2. **Upload your agreement.** Go to **Agreement templates** and drop the file onto the upload area, or choose **Browse files**. Every page appears for review.
3. **Mark the fields.** Select **Prepare fields**, then either:
   - select **Find fields with AI** and **Add field** for each suggestion you agree with, or
   - add fields yourself with the **Name, Text, Date, Signature, Initials** and **Acknowledgement** buttons, then drag them into place.

   Colours show who completes each box: **blue = client**, **orange = company countersigner**, **grey dashed = you fill in before sending** (for example a fee or start date). Select a box to change its label or who completes it.
4. **Name and publish it.** Enter a name, select **Save draft**, then **Publish version 1**. The status line at the top of the panel always says what is missing or what to do next.
5. **Add a client and a job.** Select **Choose client & job** (shown after publishing), then **New client** (name and email), then **Start a new job for this client**. Enter a job reference, tick your agreement, and select **Create job**.
6. **Review and send.** Select **Review & send** on the new agreement. Fill in any values you prepare and the company countersigner, then select **Save & send to client** (or **Send to client** if nothing changed since saving). The **Send** button stays disabled until everything needed is filled in.
7. **Share the invitation.** The **Invitation ready** pop-up opens with the signing link and access code. Select **Open client signing page** or **Copy link & code**. You can reopen it any time with **View invitation link & code**.

### As the client

8. **Open the signing link** in a private or incognito window, so the company login stays separate, and enter the access code.
9. **Review and sign.** Select **Review & sign**. Optionally open **Agreement helper** to get a summary or ask about the terms; answers cite the pages they come from. Fill each required box (**Next required field** takes you to the next one; **Fill my name and email** fills obvious name fields), tick the consent box, and select **Sign & submit**.

### Back as the company

10. **Countersign.** If the agreement has company fields, the agreement page shows **Sign for company**. Fill in the company fields and select **Sign & submit**. The Overview card (for example **“1 agreement needs you”**) also takes you there.
11. **Done.** The agreement shows **Completed** with every step ticked. Select **Download completed PDF** or **View signing history**.

To send the same agreement to another client, repeat steps 5–11. The prepared template is reused, so no fields need placing again.

## What it does

- **Clients → jobs → agreements.** Save a client once, then create a job with one or more agreements. A job is ready to start when all of its agreements are completed.
- **Reusable templates.** Prepare an agreement once and publish fixed versions. Later edits never change agreements that were already sent.
- **AI where it saves time, never in charge.** AI suggests fields on an agreement it has never seen, and answers the client's questions with page citations. People review every suggestion, signing never depends on the AI, and the AI turns off once the client has signed.
- **Signing order.** The client signs first, and the company countersigns only when the agreement needs it.
- **Checked at every step.** Missing values block sending, wrong access codes are rejected and limited, a signature must match the assigned signer, and the final PDF and signing history are saved privately.

## For developers

**Stack:** Python 3.12 and Django 5.2, with plain HTML, CSS and JavaScript (no frontend build). PDFs are processed with pypdfium2 and Pillow. DOCX files are converted to PDF in an isolated LibreOffice Docker container. The optional AI uses Groq's free plan (`openai/gpt-oss-20b`). Local development uses SQLite; production uses PostgreSQL.

### Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
.venv/Scripts/python.exe backend/manage.py migrate
.venv/Scripts/python.exe backend/provision.py company --email company@example.test --name "Demo Company"
.venv/Scripts/python.exe backend/manage.py runserver
```

The `provision.py` step asks for a password. Then open `http://127.0.0.1:8000/company/login/`.

- **AI features (optional):** create a `.env` file in the project root containing `GROQ_API_KEY=your_free_plan_key`, then restart the server. Without it, everything except the AI works.
- **DOCX uploads:** build the converter once with `docker build -t astra-docx:local backend/agreements/converter`. PDF uploads don't need Docker.

### Tests

```powershell
node --test
cd backend; ../.venv/Scripts/python.exe manage.py test
```

### Deploy

Linux server setup (Ubuntu 24.04 with Caddy, Gunicorn and PostgreSQL) and the release packager are in [`deploy/`](deploy/README.md).

### More detail

Behaviour, limits and design notes for every feature are in [`docs/details.md`](docs/details.md).
