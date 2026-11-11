// #sso_error=<code> from the backend (app/api/v1/sso.py _SSO_ERROR_CODES). Only these fixed texts are ever
// shown, so a crafted link can't put arbitrary words on the sign-in page.
const MESSAGES: Record<string, string> = {
    failed: 'SSO sign-in failed. Please try again.',
    not_enabled: 'SSO is not enabled for this organization.',
    password_tenant: 'This organization signs in with a password.',
    unavailable: 'SSO sign-in is temporarily unavailable. Please try again.',
    expired: 'Your sign-in session expired or was already used. Please start the sign-in again.',
    idp_error: 'Your identity provider did not complete the sign-in.',
    no_email: 'SSO response did not include an email address.',
    unverified: 'Your identity provider has not verified this email address.',
    not_allowed: "This account can't use single sign-on for this organization — sign in with your password.",
    deactivated: 'Your account is deactivated.',
    no_account: 'No account for this email. Ask your admin for an invitation.',
    domain_not_allowed: "This email domain can't sign up through this organization's SSO. Ask your admin for an invitation.",
    domains_unset: 'Automatic sign-up is on but no allowed email domains are set for this organization. Ask your admin.',
    guest_denied: 'This account has no active guest access here.',
    guest_other_method: "This organization's guests sign in another way.",
};

export function ssoErrorMessage(code: string | null | undefined): string {
    return code && Object.prototype.hasOwnProperty.call(MESSAGES, code) ? MESSAGES[code] : MESSAGES.failed;
}
