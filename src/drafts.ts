// Identity comes from the authenticated OIDC session. Legacy shared drafts are
// intentionally never imported into an account's private namespace.
export function draftKey(name: string) {
  return `forge-draft:${sessionStorage.getItem('forge-draft-principal') || 'local'}:${name}`;
}

export function setDraftPrincipal(
  issuer: string,
  subject: string,
  tenant: unknown,
  token?: string,
) {
  if (tenant == null && token) {
    try {
      tenant = JSON.parse(
        atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')),
      ).tenant_id;
    } catch {
      /* Opaque tokens still partition by issuer and subject. */
    }
  }
  sessionStorage.setItem(
    'forge-draft-principal',
    encodeURIComponent(JSON.stringify([issuer, subject, tenant ?? null])),
  );
}

export function clearAccountDrafts() {
  const prefix = draftKey('');
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith(prefix)) localStorage.removeItem(key);
  }
  sessionStorage.removeItem('forge-draft-principal');
}
