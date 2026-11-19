import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

export function useCatalog<T extends { id: string }>(path: string, revision?: string) {
  const [items, setItems] = useState<T[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const request = useRef<AbortController | null>(null);
  const depth = useRef(1);
  const fetchPage = useCallback(
    async (next?: string) => {
      request.current?.abort();
      const controller = new AbortController();
      request.current = controller;
      const seq = ++generation.current;
      setLoading(true);
      try {
        const pages = next ? 1 : depth.current;
        let cursor: string | null = next || null;
        let count = 0;
        const rows = new Map<string, T>();
        do {
          const result: { items: T[]; next_cursor: string | null } = await api(
            path +
              (cursor
                ? `${path.includes('?') ? '&' : '?'}cursor=${encodeURIComponent(cursor)}`
                : ''),
            'GET',
            undefined,
            undefined,
            { signal: controller.signal },
          );
          if (!Array.isArray(result.items))
            throw new Error('列表响应缺少 items，请重试或检查服务版本。');
          for (const item of result.items) rows.set(item.id, item);
          cursor = result.next_cursor;
          count++;
        } while (cursor && count < pages);
        if (seq === generation.current) {
          depth.current = next ? depth.current + 1 : count;
          const items = [...rows.values()];
          setItems((old) =>
            next
              ? [...old, ...items.filter((row) => !old.some((item) => item.id === row.id))]
              : items,
          );
          setCursor(cursor);
          setError('');
        }
      } catch (e) {
        if (seq === generation.current && !controller.signal.aborted)
          setError((e as Error).message);
      } finally {
        if (seq === generation.current) setLoading(false);
      }
    },
    [path],
  );
  useEffect(() => {
    depth.current = 1;
    setItems([]);
    setCursor(null);
  }, [path]);
  useEffect(() => {
    void fetchPage();
    return () => {
      generation.current++;
      request.current?.abort();
    };
  }, [fetchPage, revision]);
  return {
    items,
    cursor,
    loading,
    error,
    reload: () => fetchPage(),
    more: () => (cursor && !loading ? fetchPage(cursor) : undefined),
  };
}
