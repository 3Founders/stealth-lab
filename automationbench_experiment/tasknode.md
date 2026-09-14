# tasknode

1. [ ] Read the blocklist from spreadsheet 'ss_blocklist', worksheet 'ws_orgs' using sheets.spreadsheets.values.get
2. [ ] Read the SLA tiers from spreadsheet 'ss_sla', worksheet 'ws_tiers' using sheets.spreadsheets.values.get
3. [ ] Read the sync configuration from spreadsheet 'ss_config', worksheet 'ws_config' using sheets.spreadsheets.values.get
4. [ ] Retrieve new Zendesk tickets using zendesk.tickets.list
5. [ ] Filter out tickets from organizations present in the blocklist
6. [ ] For each remaining ticket, search for a matching Salesforce contact using salesforce.contacts.search
7. [ ] Determine the case priority based on the SLA tiers and the ticket's data
8. [ ] Create a Salesforce case with Origin 'Web', the matched contact, and the determined priority using salesforce.cases.create
9. [ ] Add an internal comment to the processed Zendesk ticket mentioning the account name using zendesk.tickets.comments.create
10. [ ] Post a summary message to the #support-sync Slack channel including the Batch_Reference from the sync config and the verbatim amounts/values from the processed tickets using slack.chat.postMessage
