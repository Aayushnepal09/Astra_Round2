# Astra_Round2

Planned agreement workflows for company staff and client signers.

## Local UI

Run `python backend/manage.py runserver` in the existing virtual environment, then open `http://127.0.0.1:8000/company/login/`. Company sign-in opens the protected dashboard.

All interface code is in `frontend/`. Login and dashboard each have one HTML, CSS, and JavaScript file. Shared templates, theme, controls, session helpers, and assets are in `frontend/shared/`.

Company staff can create, save, reopen, and update client-file drafts. Drafts are stored in the database and scoped to the signed-in company account. The dashboard includes company-wide counts, search, status filters, sorting, and paginated file lists. Saving a draft does not create a signing request or invitation.

Run `python backend/manage.py migrate` after pulling database changes. Local development uses the SQLite database in `backend/.local/`; deployment requires persistent database storage. Agreement uploads, field preparation, and signing are not connected yet. No new frontend dependencies are required.

## Company workflow

```mermaid
flowchart TD
    A["Company logs in to dashboard"] --> B["Open or create a client file"]
    B --> C{"Agreement template already prepared?"}

    C -- Yes --> F["Select saved agreement template"]
    C -- No --> D["Upload unfamiliar agreement"]
    D --> E["Preview pages, place fields, assign signer roles,<br/>and save a reusable template version"]
    E --> F

    F --> G["Confirm signer details and company-provided values"]
    G --> H{"Pre-send checks pass?"}
    H -- No --> I["Show setup issues for company to correct"]
    I --> G
    H -- Yes --> J["Issue signing request using a fixed agreement version"]
    J --> K["Display demo signing link, access code, expiry,<br/>and Open/Copy controls; no email is sent"]
    K --> L["Dashboard tracks signing progress"]

    L --> M{"Signing service confirms<br/>all required signatures?"}
    M -- No --> N{"Client signature confirmed,<br/>company signature required,<br/>and company not yet invited?"}
    N -- Yes --> O["Invite company countersigner"]
    O --> L
    N -- No --> P["Wait for the active signer;<br/>real email reminders are deferred"]
    P --> L

    M -- Yes --> Q["Retrieve and save completed PDF and signing history"]
    Q --> R{"Both saved successfully?"}
    R -- No --> S["Show processing issue and retry saving"]
    S --> Q
    R -- Yes --> T(["Client file: Ready to start"])
```

## Client workflow

```mermaid
flowchart TD
    A["Use signing link and access code<br/>displayed in the company demo invitation"] --> B["Open signing link"]
    B --> C["Enter access code"]
    C --> D{"Code valid for this signer and request?"}

    D -- No --> E["Retry or request a replacement code"]
    E --> C
    D -- Yes --> F["Review agreement"]

    F -. Optional help .-> G["Ask AI or request a summary:<br/>only this agreement, with citations"]
    G --> F

    F --> H["Complete required fields, acknowledgements,<br/>and signature input"]
    H --> I{"Required fields complete?"}
    I -- No --> J["Highlight missing or invalid values;<br/>preserve entered details"]
    J --> H
    I -- Yes --> K["Explicitly select Sign & submit"]

    K --> L{"Server checks pass and<br/>signing service confirms signature?"}
    L -- No --> M["Show issue and preserve entries;<br/>check uncertain submission status before retrying"]
    M --> H
    L -- Yes --> N["Your signature recorded:<br/>show confirmation"]
    N --> O(["Completed PDF available once all required signatures<br/>are confirmed and the PDF and signing history are saved"])
```

Code verification grants access; it does not sign the agreement. AI assistance is optional, and signing remains available if the assistant is unavailable.
