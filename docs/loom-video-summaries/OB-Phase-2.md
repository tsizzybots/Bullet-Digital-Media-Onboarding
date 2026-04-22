This technical specification outlines the current onboarding system for **Bullet Digital Media** (referred to as IzzyAgents for the future build), as documented in the provided screen recording.

---

## 1. Tool Inventory
| Tool | Purpose | First Seen | Role |
| :--- | :--- | :--- | :--- |
| **HighLevel (Bullet)** | Core CRM, Automation Engine, Survey Hosting | | Orchestration / Source |
| **Zapier** | Data middleware between HighLevel, G-Docs, and Asana | | Orchestration |
| **Google Sheets** | "Bullet Client Status Sheet" - Master tracking | | Data Destination |
| **Google Docs** | "OB Survey Answers" - Generated brief for kickoff | | Data Destination |
| **Slack** | Internal notifications for survey completion | | Notification |
| **Asana** | Internal task management for Finance/Tech teams | | Data Destination |
| **Stripe** | Payment processing and trigger for status updates | | Source |
| **Leadsy** | Third-party tool for Facebook Asset access | | Source/Integration |

---

## 2. End-to-End Process Map

### Stage 1: Survey Submission & Brief Generation
* **Trigger:** Client completes the "Bullet On-Boarding Survey".
* **Actions:** * HighLevel fires webhooks to Zapier and Pabbly.
    * Zapier creates a Google Doc brief from a template.
    * Client status is updated in the "Bullet Client Status Sheet".
    * Opportunity is moved to "OB Form Submitted" in the "Signed Gym Clients" pipeline.
* **Timestamp:** [04:05 - 08:40]

### Stage 2: Kickoff Call Booking
* **Trigger:** Automated email sent 30 minutes after survey completion.
* **Actions:** * Client books through a HighLevel calendar.
    * Opportunity moves to "Kickoff Call Booked".
    * Tag `kickoff-booked` added.
* **Timestamp:** [14:55 - 16:15]

### Stage 3: Asset Collection (Tech Follow-up)
* **Trigger:** Kickoff call booking triggers a branching workflow to check for missing assets.
* **Actions:** * Checks for: Ad Account access, Business Reg docs, Headshots, Brand Guidelines.
    * Sends tailored emails via Leadsy links for Facebook access.
* **Timestamp:** [20:50 - 27:30]

### Stage 4: Finance Sync & Completion
* **Trigger:** Stripe charge is successful or manual move to "Kick Off Call Complete".
* **Actions:** * Updates Asana task due date to match Kickoff date.
    * Updates master spreadsheet to "Payment Received".
* **Timestamp:** [28:00 - 29:48]

---

## 3. Zapier Workflows (Zaps)

### Zap: "2. OB Survey Complete"
* **Trigger:** Webhooks by Zapier (Catch Hook from HighLevel).
* **Action 1:** Google Docs (Create Document from Template). 
    * *Mapping:* "1. WEB | Client Business Name" → Document Name.
* **Action 2:** Google Docs (Find a Document) - Locates the newly created doc.
* **Action 3:** Google Docs (Append Text to Document).
    * *Mapping:* Full dump of survey answers into the doc body.
* **Action 4:** Slack (Send Channel Message).

### Zap: "Kickoff Call Booked - Update CSS..."
* **Trigger:** Webhook from HighLevel.
* **Action 1:** Asana (Find Task in Project: "Finance").
* **Action 2:** Asana (Update Task).
    * *Mapping:* `appointment.start_time` → Due Date.

### Zap: "4. Payment Received - Update CSS"
* **Trigger:** Stripe (New Charge).
* **Action 1:** Google Sheets (Lookup Spreadsheet Row) via Email.
* **Action 2:** Google Sheets (Update Spreadsheet Row).
    * *Mapping:* Value "4. Payment Received" → Status Column.

---

## 4. Google Sheets Structures
**Spreadsheet:** "Bullet Client Status Sheet"
**Tab:** "Live Clients 2.0"
* **Columns:**
    1.  `CLIENT / BUSINESS NAME` (Text)
    2.  `Monthly Ad Budget` (Currency)
    3.  `Status` (Dropdown: 1. Lead Gen Live, 2. OB Form Submitted, etc.)
    4.  `Legal Entity` (Text)
    5.  `DS` [Digital Specialist] (Dropdown)
    6.  `PD` [Performance Director] (Dropdown)
    7.  `HL Version` (Number)
    8.  `Notice Period` (Text)
    9.  `SaaS Mode` (Text)

---

## 5. Forms & Data Entry Points
* **Bullet On-boarding Survey:** Hosted in HighLevel.
* **Key Data Collected:** Business Info, Member count, Target demographics, Competitor URLs, USP, Ad budget goals, Brand colors/fonts.
* **Sales Handover Notes:** Manually pasted into the generated Google Doc brief by the sales team post-call.

---

## 6. Notifications & Communications
* **Survey Reminder:** HighLevel email sent 30 mins post-sign if survey not done.
* **Kickoff Confirmation:** Email with subject "Kick Off Call - Confirmation & Next Steps".
* **Asset Request Email:** Complex branching email based on missing technical elements.
* **Internal Slack:** Alerts to `bullet_inbound_clients` when kickoff is booked.

---

## 7. Manual Steps
* **Sales Notes Integration:** Sales team must manually open the G-Doc brief and paste "Sales Handover Notes" at the top.
* **Monday Audit:** Narrator manually reviews kickoff dates every Monday to fix Asana sync issues.
* **Workflow Triggering:** Moving opportunities between stages often requires manual drag-and-drop to trigger "Stage Changed" events.

---

## 8. Pain Points & Inefficiencies
* **Broken Automations:** Narrator admits the Pabbly webhook for sub-account creation is "broken" or redundant.
* **Asana Sync Issues:** The narrator explicitly calls the kickoff-to-payment-date sync "irregular and doesn't really work".
* **Maintenance Nightmare:** The "Outstanding Elements" branching workflow is visually massive and difficult to debug/update.
* **Manual Monday Work:** The need for manual "Monday audits" to ensure Finance/Asana dates are correct.

---

## 9. Data Dictionary (Key Fields)
* `client_name`: Originates in HighLevel Contact.
* `kickoff_date`: Originates in HighLevel Calendar.
* `ad_budget`: Originates in Survey.
* `shared_folder_link`: Custom field in HighLevel.
* `onboarding_status`: Moves between HighLevel Pipeline, G-Sheets Status column, and Asana task status.

---

## 10. Open Questions
1.  What specific logic is Pabbly supposed to handle regarding "New Sub Account" creation?
2.  What is the "Lead Gen Live" status trigger specifically (Manual or automated)?
3.  Are there specific validation rules for the Certificate of Incorporation upload?

---

## 11. Rebuild Recommendations
* **State Management:** Replace HighLevel Pipelines with a formal State Machine in the backend (e.g., AASM or a database `status` enum).
* **Brief Generation:** Instead of Zapier + G-Docs, use a PDF generation library (like Puppeteer or WickedPDF) to generate the brief directly from the DB.
* **Asset Portal:** Build a "Client Asset Dashboard" to replace the 16-branch HighLevel workflow; show clients a checklist of what is missing.
* **Asana/Stripe Sync:** Use direct Webhook listeners in the new app code to update a centralized `tasks` table, eliminating the need for Zapier middleware.