// Identity comes from the authenticated OIDC session. Legacy shared drafts are
// intentionally never imported into an account's private namespace.
export function draftKey(name: string) {
  return `forge-draft:${sessionStorage.getItem('forge-draft-principal') || 'local'}:${name}`;
}

export type ChatDraft = {
  text: string;
  manualBudget?: number;
  previousText?: string;
  legacyBudget?: number;
};

function chatProjectKey(project: string) {
  return draftKey('chat/project/' + encodeURIComponent(project));
}

export function initialChatProject(scope: string): string {
  try {
    if (scope !== '所有项目')
      return history.state?.composerScope === draftKey(scope)
        ? history.state.composerProject
        : scope;
    const last = localStorage.getItem(draftKey('chat/selection'));
    if (last !== null) return last;
    const legacy = JSON.parse(localStorage.getItem(draftKey('chat')) || 'null');
    return typeof legacy?.project === 'string' ? legacy.project : '';
  } catch {
    return scope === '所有项目' ? '' : scope;
  }
}

export function rememberChatProject(scope: string, project: string) {
  try {
    if (scope === '所有项目') localStorage.setItem(draftKey('chat/selection'), project);
    else
      history.replaceState(
        { ...history.state, composerScope: draftKey(scope), composerProject: project },
        '',
      );
  } catch {}
}

export function readChatDraft(project: string): ChatDraft {
  try {
    const saved = JSON.parse(localStorage.getItem(chatProjectKey(project)) || 'null');
    if (typeof saved?.text === 'string') return saved;
    const legacy = JSON.parse(localStorage.getItem(draftKey('chat')) || 'null');
    if (legacy?.project === project && typeof legacy.text === 'string') {
      // Older drafts stored the temporary workspace default as if the user had edited it.
      return {
        text: legacy.text,
        legacyBudget: Number.isFinite(legacy.budget) ? legacy.budget : undefined,
      };
    }
  } catch {}
  return { text: '' };
}

export function writeChatDraft(project: string, draft?: ChatDraft) {
  try {
    if (draft) localStorage.setItem(chatProjectKey(project), JSON.stringify(draft));
    else localStorage.removeItem(chatProjectKey(project));
    const legacy = JSON.parse(localStorage.getItem(draftKey('chat')) || 'null');
    if (legacy?.project === project) {
      if (localStorage.getItem(draftKey('chat/selection')) === null)
        localStorage.setItem(draftKey('chat/selection'), project);
      localStorage.removeItem(draftKey('chat'));
    }
  } catch {}
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
