This technical specification outlines the current onboarding system for **Bullet Digital Media** (referred to in the prompt as IzzyAgents), based on the provided walkthrough.

---

## 1. Tool Inventory
| Tool | Purpose | First Seen | Role |
| :--- | :--- | :--- | :--- |
| **PandaDoc** | Contract generation and electronic signature. | | Data Source (Trigger) |
| **Zapier** | Primary orchestration layer for multi-app syncing. | | Orchestrator |
| **Pabbly** | Workaround for creating HighLevel sub-accounts. | | Destination / API bridge |
| **Google Sheets** | Master client status tracking. | | Database (Legacy) |
| **Slack** | Internal team notification for signed contracts. | | Communication |
| **Xero** | Accounting; contact creation and invoice drafting. | | Destination |
| **Google Drive** | Automated client folder structure creation. | | File Management |
| **LeadConnector (GHL)** | CRM, Opportunity Pipeline, and Onboarding Survey. | | CRM / Data Source |
| **Asana** | Project management and task templates. | | Execution Layer |
| **Timely** | Time tracking for client projects. | | Destination |

---

## 2. End-to-End Process Map
1.  **Contract Execution [00:00 - 01:42]:**
    * **Purpose:** Secure legal agreement and payment details.
    * **Trigger:** Manual creation of PandaDoc from template: `"NEW CLIENT & Bullet Digital Media - Digital Marketing Partnership Agreement"`.
    * **Outputs:** Signed PDF, Stripe Customer ID.
2.  **Automated Internal Setup [01:42 - 12:29]:**
    * **Purpose:** Provisioning accounts and notifying departments.
    * **Trigger:** PandaDoc `"Document Completed"`.
    * **Actor:** Automated (Zapier).
    * **Outputs:** GHL Sub-account, GSheets row, Xero Invoice, GDrive folder tree, Asana project.
3.  **Customer Intake (Survey) [13:40 - 15:58]:**
    * **Purpose:** Gather technical and brand data.
    * **Trigger:** GHL tag `"signed"` added to contact.
    * **Actor:** Human (Client).
    * **Inputs:** `"Bullet Onboarding - V2"` survey submission.
4.  **Onboarding Verification [16:07 - 21:55]:**
    * **Purpose:** Review survey data and book Kick-Off call.
    * **Actor:** Human (Team review).

---

## 3. Zapier Workflows (Zaps)
**Zap Name:** `"NEW CLIENT & Bullet Digital Media - Digital Marketing Partnership Agreement"`

* **Trigger:** PandaDoc — `"Document Completed"`.
* **Action 2: Webhooks by Zapier:** POST to Pabbly.
    * *Mappings:* `"Business Name"`, `"Email"`, `"Address"`, `"City"`, `"State"`, `"Post Code"`, `"Country"`.
* **Action 3: Google Sheets:** `"Create Spreadsheet Row"` in `"Bullet Clients Status Sheet"`.
* **Action 4: Slack:** `"Send Channel Message"` to `#bullet_inbound_clients`.
    * *Content:* `"Name: [Client Name] has just signed their agreement. Let's rock and roll!"`
* **Action 5: Xero:** `"Create/Update Contact"`.
* **Action 7: Xero:** `"Create Sales Invoice"` (Draft).
    * *Logic:* Pulls Management Fee from Line Items Subtotal.
* **Action 8-32: Google Drive:** Creates specific sub-folder structure:
    * `"Video"` > `"Face to Camera"` > `"WWW"`, `"PAS"`, `"BAB"`, `"Value Stack"`, `"Objection Handler"`.
* **Action 44: Asana:** `"Create Project from Template"`.
* **Action 45: Asana:** `"Create Finance Task"`.
* **Action 46: Timely:** `"Create Client"`.
* **Action 47: Timely:** `"Create Project"`.

---

## 4. Google Sheets Structures
**Spreadsheet:** `"Bullet Clients Status Sheet"`
* **Columns:**
    * `Site / Account`: (Text) - Branch ID or location.
    * `Client / Business Name`: (Text).
    * `Paid 1st Payment / Monthly Payment Day`: (Date/Currency).
    * `Monthly Ad Budget`: (Currency).
    * `Status`: (Dropdown) e.g., `"Lead Gen Live"`, `"In Set Up"`.
    * `Legal Entity (Finance)`: (Text).
    * `HL Version`: (Number) e.g., `2.0`.
    * `Notice Terms`: (Text) e.g., `"1 Calendar Month"`.

---

## 5. Forms & Data Entry Points
* **Agreement Upsells:** Checkboxes for `"Conversation AI Lead Assistant"` (£99) and `"Email Database Reactivation"` (£149).
* **Onboarding Survey V2 (GHL):**
    * `The Basics`: Full name, Facility name, Website, Social handles.
    * `Member Data`: Current members, Target goal, Member capacity.
    * `Marketing`: Pain points, Aspirations, USPs, Competitors.
    * `Technical`: FB Business Manager access, Website backend access, Web developer contact.

---

## 6. Notifications & Communications
* **Internal Slack:** Notifies team of new signature.
* **Onboarding Email:** Triggered by GHL `"signed"` tag. Contains link to survey intro page.
* **Reminder Sequence:** GHL wait steps (48 hours, then 24 hours) if survey is not completed.
* **Internal Chaser:** Notification to staff (Mike/Kate) after 72 hours of inactivity.

---

## 7. Manual Steps
* **Sub-account Maintenance:** Human intervention needed to delete duplicate sub-accounts for returning clients.
* **Invoice Finalization:** Finance team must finalize the draft invoice in Xero.
* **Asana Project Customization:** Splitting tasks between Tech, Creative, and Digital Specialists.
* **Timely Project Creation:** The project part of Timely setup is currently manual.

---

## 8. Pain Points & Inefficiencies
* **API Limitations:** Using Pabbly as a "middleman" because Zapier cannot natively create GHL sub-accounts.
* **Data Redundancy:** Client has to repeat their name and business name in the survey despite already providing it in the contract.
* **Convoluted Survey Logic:** The narrator notes the survey is "convoluted" because it allocates information across multiple people in a non-linear way.
* **Manual Stripe Connection:** System doesn't accept Amex, requiring manual extraction of payment details outside the flow.

---

## 9. Data Dictionary
| Field Name | Source | Storage | Consumption |
| :--- | :--- | :--- | :--- |
| `Business Name` | PandaDoc | GSheets, GHL, Xero, GDrive | All provisioning |
| `Stripe Customer ID`| PandaDoc | GHL, Xero | Billing |
| `Management Fee` | PandaDoc | Xero (Invoice) | Finance |
| `FB Access Level` | Survey V2 | GHL (Custom Field) | Tech Setup |
| `Pain Points` | Survey V2 | GHL (Custom Field) | Creative / Ad Copy |

---

## 10. Open Questions
* How is the initial data pushed from HubSpot to PandaDoc?
* What specific logic determines if a client is "International" vs. "UK" for account routing?
* Where are the survey submissions stored if GHL isn't creating the custom fields correctly?

---

## 11. Rebuild Recommendations
* **Zapier Orchestration → Node.js/Python Microservice:** Replace 47+ steps with a structured script using tool SDKs.
* **Pabbly Bridge → Direct API Integration:** Use GHL API directly to provision sub-accounts, removing the extra cost of Pabbly.
* **Google Sheets Status → SQL Database:** Move `"Bullet Clients Status Sheet"` to a `clients` table with relational links to `invoices` and `tasks`.
* **GHL Survey → Integrated React Form:** Build a custom dashboard form that pre-populates data from the PandaDoc webhook to avoid redundant data entry.