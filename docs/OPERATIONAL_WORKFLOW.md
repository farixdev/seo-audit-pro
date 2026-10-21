# Agency operational workflow

1. **Set up** (Manager): create teams, then add Team Leads and Agents. Each new
   account receives a temporary password shown once; the person must choose
   their own password at first sign-in. Configure SMTP in Settings.
2. **Build the lead bank**: add leads one by one or import CSV/XLSX files
   (Manager: any team; Team Lead: own team). Websites are normalised
   (`http://www.example.com/` = `example.com`), so a business can exist only once
   across the agency; matching emails, phones or company names raise a warning.
3. **Assign**: Managers assign leads to any team or person; Team Leads assign
   within their team. Every change is recorded with who, when, from, to and why.
   Two people assigning the same lead at the same time cannot both win.
4. **Audit**: from a lead, start an audit. The real engine crawls the site and
   reports progress live (pages processed and discovered, current step). One
   audit per lead can run at a time.
5. **Review**: overall and category scores, the issue explorer (mark issues
   resolved), the passive security assessment, recommendations by priority, and
   crawled pages.
6. **Deliver**: open or download the client report (DOCX; PDF where the server
   supports it; HTML), or the technical report.
7. **Outreach**: generate an email from the audit's actual findings, edit it,
   attach the report and send. Bulk outreach previews the recipients and needs
   explicit confirmation; emails are sent at a safe rate and their delivery
   status is tracked (draft, queued, sent, failed).
8. **Collaborate**: direct messages and team channels; notifications for new
   leads, reassignments, completed audits, ready reports, messages and email
   results.
9. **Oversee** (Manager): dashboard, team performance, and the immutable audit
   log of security-sensitive actions.
