import { useEffect, useState, type ReactNode } from 'react';
import { UserManager, WebStorageStateStore } from 'oidc-client-ts';

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'loading' | 'ready' | 'login' | 'error'>('loading');
  const [error, setError] = useState('');
  const [manager, setManager] = useState<UserManager | null>(null);
  useEffect(() => {
    let alive = true;
    let current: UserManager | null = null;
    const ready = async () => {
      const response = await fetch('/v1/auth/config', { signal: AbortSignal.timeout(15000) });
      if (!response.ok) throw new Error('登录配置暂时不可用');
      const config = await response.json();
      if (!alive) return;
      if (config.mode === 'local') {
        setState('ready');
        return;
      }
      current = new UserManager({
        authority: config.authority,
        client_id: config.client_id,
        scope: config.scope,
        response_type: 'code',
        redirect_uri: location.origin + '/auth/callback',
        post_logout_redirect_uri: location.origin + '/',
        userStore: new WebStorageStateStore({ store: sessionStorage }),
        stateStore: new WebStorageStateStore({ store: sessionStorage }),
        automaticSilentRenew: true,
        loadUserInfo: false,
        extraQueryParams: config.audience ? { audience: config.audience } : undefined,
      });
      setManager(current);
      current.events.addUserLoaded((user) => {
        sessionStorage.setItem('forge-access-token', user.access_token);
        if (alive) setState('ready');
      });
      current.events.addAccessTokenExpired(() => {
        sessionStorage.removeItem('forge-access-token');
        if (alive) setState('login');
      });
      current.events.addSilentRenewError(() => {
        sessionStorage.removeItem('forge-access-token');
        if (alive) setState('login');
      });
      let user;
      if (location.pathname === '/auth/callback') {
        user = await current.signinRedirectCallback();
        history.replaceState(
          {},
          '',
          typeof user.state === 'string' &&
            user.state.startsWith('/') &&
            !user.state.startsWith('//')
            ? user.state
            : '/',
        );
      } else user = await current.getUser();
      if (!alive) return;
      if (user && !user.expired) {
        sessionStorage.setItem('forge-access-token', user.access_token);
        setState('ready');
      } else {
        sessionStorage.removeItem('forge-access-token');
        setState('login');
      }
    };
    void ready().catch((e) => {
      if (alive) {
        setError(e instanceof Error ? e.message : '登录失败');
        setState('error');
      }
    });
    return () => {
      alive = false;
      current?.stopSilentRenew();
    };
  }, []);
  if (state === 'ready')
    return (
      <>
        {manager && (
          <button
            className="auth-logout"
            onClick={() => {
              sessionStorage.removeItem('forge-access-token');
              setState('login');
              void manager.removeUser();
            }}
          >
            退出登录
          </button>
        )}
        {children}
      </>
    );
  return (
    <main className="auth-screen">
      <h1>ForgeAgent</h1>
      <p role={state === 'error' ? 'alert' : 'status'}>
        {state === 'loading'
          ? '正在连接身份服务…'
          : state === 'error'
            ? error
            : '请使用工作空间身份登录。'}
      </p>
      {manager && (
        <button
          className="button primary"
          onClick={() => {
            setError('');
            void manager
              .signinRedirect({ state: location.pathname + location.search })
              .catch((e) => {
                setError(e.message);
                setState('error');
              });
          }}
        >
          登录工作空间
        </button>
      )}
      {state === 'error' && !manager && <button onClick={() => location.reload()}>重新连接</button>}
    </main>
  );
}
