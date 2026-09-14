# run: smoke-2

1. [x] Search for the email from the contact using a query filter to identify the relevant message.
2. [x] Retrieve the full content of the identified email to extract the updated phone number.  deps=[1]
3. [x] Search for the corresponding contact record in Salesforce using the contact's name or email.
4. [x] Update the phone field in the Salesforce contact record with the new value from the email.  deps=[2,3]
