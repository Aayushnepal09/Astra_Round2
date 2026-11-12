# Astra_Round2

Planned agreement workflows for company staff and client signers.

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
    J --> K["Email client signer a unique signing link and access code"]
    K --> L["Dashboard tracks signing progress"]

    L --> M{"Signing service confirms<br/>all required signatures?"}
    M -- No --> N{"Client signature confirmed,<br/>company signature required,<br/>and company not yet invited?"}
    N -- Yes --> O["Invite company countersigner"]
    O --> L
    N -- No --> P["Wait for the active signer;<br/>send scheduled reminders only while pending"]
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
    A["Receive email with signing link and access code"] --> B["Open signing link"]
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
