With reference to our previous conversations regarding AI use cases

please note that the following use cases have been discussed so far:

- Ability for the AI to write narratives in the Fedramp Standard Document.
- Ability to fetch asset/infrastructure details from AWS Account.
- Ability for AI to analyze security documentation and point out narrative that aligns to security control
- Ability for AI to analyze system components and design boundary diagrams and dataflow diagrams
- Identify where sensitive data may be stored (e.g., federal data for FedRAMP and CUI/FCI for CMMC)
- Ability for AI to review evidence and provide feedback on what the evidence is showing and whether or not its sufficient
- Provide a similar tool to AWS System Manager that can map technical solution/vendor to different categories (OS, IAM, FIM, Ticketing, etc.) See table below.
- Analyze compliance scan results and map the fail results to applicable control. For example, no warning banner in STIGS may map to AC-8 fail
- AI should be able map back evidence, artifact, document to the specific control implementation (e.g. a screenshot from AD Users to control AC-2)
- AI should have capability to generate document if it is not available (e.g. Policy, Procedure) and place into appropriate repository location.
- Be able to validate and review current deviation/exception (operational requirement, vendor dependency, false positive) list. For example, a vulnerability originally categorized as a vendor dependency may have a released fix where the cloud service provider can apply to remediate the identified issue.

If there are any other use cases please share with us at your earliest

convenience. If these are the only two we will consider the scope of

AI implementation locked to these two use cases only.

For Example: <https://aws.amazon.com/blogs/mt/preventing-blacklisted-applications-with-aws-systems-manager-and-aws-config/>

| **Category** | **Technical Solution / Vendor** |
| --- | --- |
| Operating Systems | RHEL, Windows |
| --- | --- |
| IAM/Access Management | LDAP, AWS IAM |
| --- | --- |
| Endpoint/Antivirus (AV), File Integrity Monitoring (FIM) | CrowdStrike, Cortex XDR |
| --- | --- |
| Code Repository | GitHub |
| --- | --- |
| Ticketing | Jira, ServiceNow |
| --- | --- |
| Configuration Management | GitHub, AWS EC2 |
| --- | --- |
| Firewall/VPN | AWS Network Firewall, Security Groups, GVPN, WAF |
| --- | --- |
| MFA | YubiKey, Microsoft Authenticator |
| --- | --- |
| SIEM | Splunk |
| --- | --- |
| Secrets Management | AWS Secrets Manager |
| --- | --- |
| Vulnerability Scanning | Nessus, Fortify |
| --- | --- |
