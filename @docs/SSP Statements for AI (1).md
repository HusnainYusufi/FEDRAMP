## AC-2 Account Management (L)(M)(H) - Low

> a\. Define and document the types of accounts allowed and specifically
> prohibited for use within the system;
>
> b\. Assign account managers;
>
> c\. Require \[Assignment: organization-defined prerequisites and
> criteria\] for group and role membership;
>
> d\. Specify:
>
> 1\. Authorized users of the system;
>
> 2\. Group and role membership; and
>
> 3\. Access authorizations (i.e., privileges) and \[Assignment:
> organization-defined attributes (as required)\] for each account;
>
> e\. Require approvals by \[Assignment: organization-defined personnel
> or roles\] for requests to create accounts;
>
> f\. Create, enable, modify, disable, and remove accounts in accordance
> with \[Assignment: organization-defined policy, procedures,
> prerequisites, and criteria\];
>
> g\. Monitor the use of accounts;
>
> h\. Notify account managers and \[Assignment: organization-defined
> personnel or roles\] within:
>
> 1\. \[FedRAMP Assignment: twenty-four (24) hours\] when accounts are
> no longer required;
>
> 2\. \[FedRAMP Assignment: eight (8) hours\] when users are terminated
> or transferred; and
>
> 3\. \[FedRAMP Assignment: eight (8) hours\] when system usage or
> need-to-know changes for an individual;
>
> i\. Authorize access to the system based on:
>
> 1\. A valid access authorization;
>
> 2\. Intended system usage; and
>
> 3\. \[Assignment: organization-defined attributes (as required)\];
>
> j\. Review accounts for compliance with account management
> requirements \[FedRAMP Assignment: monthly for privileged accessed,
> every six (6) months for non-privileged access\];
>
> k\. Establish and implement a process for changing shared or group
> account authenticators (if deployed) when individuals are removed from
> the group; and
>
> l\. Align account management processes with personnel termination and
> transfer processes.

+-----------------------------------------------------------------------+
| > **AC-2 Control Summary Information**                                |
+=======================================================================+
| > Responsible Role: Infrastructure, GRC, Account Manager, Customer    |
+-----------------------------------------------------------------------+
| > Parameter AC-2(c): Account provisioning/deprovisioning process for  |
| > FedRAMP accounts                                                    |
+-----------------------------------------------------------------------+
| > Parameter AC-2(d)(3): Dragon attributes                             |
+-----------------------------------------------------------------------+
| > Parameter AC-2(e): Group Owners and ISSO                            |
+-----------------------------------------------------------------------+
| > Parameter AC-2(f): Access Control policies and procedures           |
+-----------------------------------------------------------------------+
| > Parameter AC-2(h): Group Owners                                     |
+-----------------------------------------------------------------------+
| > Parameter AC-2(h)(1): twenty-four (24) hours\] when accounts are no |
| > longer required                                                     |
+-----------------------------------------------------------------------+
| > Parameter AC-2(h)(2): eight (8) hours                               |
+-----------------------------------------------------------------------+
| > Parameter AC-2(h)(3): eight (8) hours                               |
+-----------------------------------------------------------------------+
| > Parameter AC-2(i)(3): Dragon account attributes (as required)       |
+-----------------------------------------------------------------------+
| > Parameter AC-2(j): monthly for privileged accessed, every six (6)   |
| > months for non-privileged access                                    |
+-----------------------------------------------------------------------+
| > Implementation Status (check all that apply):                       |
| >                                                                     |
| > ☒ Implemented                                                       |
| >                                                                     |
| > ☐ Partially Implemented                                             |
| >                                                                     |
| > ☐ Planned                                                           |
| >                                                                     |
| > ☐ Alternative implementation                                        |
| >                                                                     |
| > ☐ Not Applicable                                                    |
+-----------------------------------------------------------------------+
| > Control Origination (check all that apply):                         |
| >                                                                     |
| > ☐ Service Provider Corporate                                        |
| >                                                                     |
| > ☐ Service Provider System Specific                                  |
| >                                                                     |
| > ☐ Service Provider Hybrid (Corporate and System Specific)           |
| >                                                                     |
| > ☐ Configured by Customer (Customer System Specific)                 |
| >                                                                     |
| > ☐ Provided by Customer (Customer System Specific)                   |
| >                                                                     |
| > ☒ Shared (Service Provider and Customer Responsibility)             |
| >                                                                     |
| > ☒ Inherited from pre-existing FedRAMP Authorization for             |
| > AGENCYAMAZONEW, 05/01/2023                                          |
+-----------------------------------------------------------------------+

+-----------------------------------------------------------------------+
| > **AC-2 What is the solution and how is it implemented?**            |
+=======================================================================+
| Part a:                                                               |
|                                                                       |
| Dragon has defined and documented the types of accounts allowed and   |
| specifically prohibited for use within the system.                    |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for defining and documenting the     |
| types of accounts allowed and specifically prohibited for use within  |
| the system.                                                           |
+-----------------------------------------------------------------------+
| Part b:                                                               |
|                                                                       |
| Dragon has assigned account managers for DGC.                         |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for assigning their own account      |
| managers.                                                             |
+-----------------------------------------------------------------------+
| Part c:                                                               |
|                                                                       |
| Dragon requires an account provisioning/deprovisioning process for    |
| all FedRAMP accounts.                                                 |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for requiring organization-defined   |
| prerequisites and criteria\] for group and role membership.           |
+-----------------------------------------------------------------------+
| Part d:                                                               |
|                                                                       |
| Dragon specifies:                                                     |
|                                                                       |
| 1\. Authorized users of the system;                                   |
|                                                                       |
| 2\. Group and role membership; and                                    |
|                                                                       |
| 3\. Access authorizations (i.e., privileges) and Dragon associated    |
| account attributes                                                    |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023)                                          |
|                                                                       |
| Dragon customers are responsible for specifying:                      |
|                                                                       |
| > 1\. Authorized users of the system;                                 |
| >                                                                     |
| > 2\. Group and role membership; and                                  |
| >                                                                     |
| > 3\. Access authorizations (i.e., privileges) and \[Assignment:      |
| > organization-defined attributes (as required)\] for each account;   |
+-----------------------------------------------------------------------+
| Part e:                                                               |
|                                                                       |
| Dragon requires approvals by account managers and group owners for    |
| requests to create FedRAMP associated accounts.                       |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for requiring approvals by           |
| \[Assignment: organization-defined personnel or roles\] for requests  |
| to create accounts;\].                                                |
+-----------------------------------------------------------------------+
| Part f:                                                               |
|                                                                       |
| Dragon creates, enables, modifies, disable, and removes accounts in   |
| accordance with FedRAMP access control policies and procedures.       |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for creating, enabling, modifying,   |
| disabling, and removing accounts in accordance with \[Assignment:     |
| organization-defined policy, procedures, prerequisites, and           |
| criteria\].                                                           |
+-----------------------------------------------------------------------+
| Part g:                                                               |
|                                                                       |
| Dragon notifies account managers and group owners within:             |
|                                                                       |
| 1\. \[FedRAMP Assignment: twenty-four (24) hours\] when accounts are  |
| no longer required;                                                   |
|                                                                       |
| 2\. \[FedRAMP Assignment: eight (8) hours\] when users are terminated |
| or transferred; and                                                   |
|                                                                       |
| 3\. \[FedRAMP Assignment: eight (8) hours\] when system usage or      |
| need-to-know changes for an individual;                               |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023)                                          |
|                                                                       |
| Dragon customers are responsible for notifying account managers and   |
| \[Assignment: organization-defined personnel or roles\] within:       |
|                                                                       |
| 1\. \[FedRAMP Assignment: twenty-four (24) hours\] when accounts are  |
| no longer required;                                                   |
|                                                                       |
| 2\. \[FedRAMP Assignment: eight (8) hours\] when users are terminated |
| or transferred; and                                                   |
|                                                                       |
| 3\. \[FedRAMP Assignment: eight (8) hours\] when system usage or      |
| need-to-know changes for an individual;                               |
+-----------------------------------------------------------------------+
| Part h:                                                               |
|                                                                       |
| Dragon monitors the use of accounts.                                  |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023)                                          |
|                                                                       |
| Dragon customers are responsible for monitoring the use of accounts.  |
+-----------------------------------------------------------------------+
| Part i:                                                               |
|                                                                       |
| Dragon authorizes access to the system based on:                      |
|                                                                       |
| 1\. A valid access authorization;                                     |
|                                                                       |
| 2\. Intended system usage; and                                        |
|                                                                       |
| 3\. Dragon account attributes (as required)                           |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for authorizing access to the system |
| based on:                                                             |
|                                                                       |
| 1\. A valid access authorization;                                     |
|                                                                       |
| 2\. Intended system usage; and                                        |
|                                                                       |
| 3\. \[Assignment: organization-defined attributes (as required)\];    |
+-----------------------------------------------------------------------+
| Part j:                                                               |
|                                                                       |
| Dragon review accounts for compliance with account management         |
| requirements monthly for privileged accessed, every six (6) months    |
| for non-privileged access                                             |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023)                                          |
|                                                                       |
| Dragon customers are responsible for reviewing accounts for           |
| compliance with account management requirements \[FedRAMP Assignment: |
| monthly for privileged accessed, every six (6) months for             |
| non-privileged access\]                                               |
+-----------------------------------------------------------------------+
| Part k:                                                               |
|                                                                       |
| Dragon has established and implemented a process for changing shared  |
| or group account authenticators (if deployed) when individuals are    |
| removed from the group.                                               |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for establishing and implementing a  |
| process for changing shared or group account authenticators (if       |
| deployed) when individuals are removed from the group.                |
+-----------------------------------------------------------------------+
| Part l:                                                               |
|                                                                       |
| Dragon aligns account management processes with personnel termination |
| and transfer processes.                                               |
|                                                                       |
| Dragon partially inherits this control for account management as DGC  |
| is hosted on AWS East/West which is FedRAMP authorized                |
| (AGENCYAMAZONEW, 05/01/2023).                                         |
|                                                                       |
| Dragon customers are responsible for aligning account management      |
| processes with personnel termination and transfer processes.          |
+-----------------------------------------------------------------------+

## SC-13 Cryptographic Protection (L)(M)(H)

> a\. Determine the \[Assignment: organization-defined cryptographic
> uses\]; and
>
> b\. Implement the following types of cryptography required for each
> specified cryptographic use: \[FedRAMP Assignment: FIPS-validated or
> NSA-approved cryptography\].
>
> **SC-13 Additional FedRAMP Requirements and Guidance:**
>
> **Guidance:** This control applies to all use of cryptography. In
> addition to encryption, this includes functions such as hashing,
> random number generation, and key generation. Examples include the
> following:
>
> · Encryption of data
>
> · Decryption of data
>
> · Generation of one time passwords (OTPs) for MFA
>
> · Protocols such as TLS, SSH, and HTTPS
>
> The requirement for FIPS 140 validation, as well as timelines for
> acceptance of FIPS 140-2, and 140-3 can be found at the NIST
> Cryptographic Module Validation Program (CMVP).
> [[https://csrc.nist.gov/projects/cryptographic-module-validation-program]{.underline}](https://csrc.nist.gov/projects/cryptographic-module-validation-program).
>
> **Guidance:** For NSA-approved cryptography, the National Information
> Assurance Partnership (NIAP) oversees a national program to evaluate
> Commercial IT Products for Use in National Security Systems. The NIAP
> Product Compliant List can be found at the following location:
> [[https://www.niap-ccevs.org/Product/index.cfm]{.underline}](https://www.niap-ccevs.org/Product/index.cfm).
>
> **Guidance:** When leveraging encryption from underlying IaaS/PaaS:
> While some IaaS/PaaS provide encryption by default, many require
> encryption to be configured, and enabled by the customer. The CSP has
> the responsibility to verify encryption is properly configured.
>
> **Guidance:** Moving to non-FIPS CM or product is acceptable when:
>
> · FIPS validated version has a known vulnerability
>
> · Feature with vulnerability is in use
>
> · Non-FIPS version fixes the vulnerability
>
> · Non-FIPS version is submitted to NIST for FIPS validation
>
> · POA&M is added to track approval, and deployment when ready
>
> **Guidance:** At a minimum, this control applies to cryptography in
> use for the following controls: AU-9(3), CP-9(8), IA-2(6), IA-5(1),
> MP-5, SC-8(1), and SC-28(1).

+-----------------------------------------------------------------------+
| > **SC-13 Control Summary Information**                               |
+=======================================================================+
| > Responsible Role: Infrastructure, Service Teams, DBAs               |
+-----------------------------------------------------------------------+
| > Parameter SC-13(a): All cryptographic areas (data at rest, data in  |
| > transit, remote access, authentication, etc.) as required by        |
| > FedRAMP                                                             |
+-----------------------------------------------------------------------+
| > Parameter SC-13(b): FIPS-validated or NSA-approved cryptography     |
+-----------------------------------------------------------------------+
| > Implementation Status (check all that apply):                       |
| >                                                                     |
| > ☒ Implemented                                                       |
| >                                                                     |
| > ☐ Partially Implemented                                             |
| >                                                                     |
| > ☐ Planned                                                           |
| >                                                                     |
| > ☐ Alternative implementation                                        |
| >                                                                     |
| > ☐ Not Applicable                                                    |
+-----------------------------------------------------------------------+
| > Control Origination (check all that apply):                         |
| >                                                                     |
| > ☐ Service Provider Corporate                                        |
| >                                                                     |
| > ☒ Service Provider System Specific                                  |
| >                                                                     |
| > ☐ Service Provider Hybrid (Corporate and System Specific)           |
| >                                                                     |
| > ☐ Configured by Customer (Customer System Specific)                 |
| >                                                                     |
| > ☐ Provided by Customer (Customer System Specific)                   |
| >                                                                     |
| > ☐ Shared (Service Provider and Customer Responsibility)             |
| >                                                                     |
| > ☒ Inherited from pre-existing FedRAMP Authorization for             |
| > AGENCYAMAZONEW, 05/01/2023                                          |
+-----------------------------------------------------------------------+

+-----------------------------------------------------------------------+
| > **SC-13 What is the solution and how is it implemented?**           |
+=======================================================================+
| Part a:                                                               |
|                                                                       |
| Dragon has determined the cryptographic uses for the DGC FedRAMP      |
| system.                                                               |
|                                                                       |
| Dragon partially inherits this control as DGC is hosted on AWS        |
| East/West which is FedRAMP authorized (AGENCYAMAZONEW, 05/01/2023).   |
|                                                                       |
| Dragon customers are responsible for determining the \[Assignment:    |
| organization-defined cryptographic uses\].                            |
+-----------------------------------------------------------------------+
| Part b:                                                               |
|                                                                       |
| Dragon implements the following types of cryptography required for    |
| each specified cryptographic use: FIPS-validated or NSA-approved      |
| cryptography                                                          |
|                                                                       |
| Dragon partially inherits this control as DGC is hosted on AWS        |
| East/West which is FedRAMP authorized (AGENCYAMAZONEW, 05/01/2023).   |
|                                                                       |
| Dragon customers are responsible for implementing the following types |
| of cryptography required for each specified cryptographic use:        |
| \[FedRAMP Assignment: FIPS-validated or NSA-approved cryptography\].  |
+-----------------------------------------------------------------------+
