import { useCallback, useEffect, useRef, useState } from "react";
import { onRetry } from "../api/client.js";

export function useMedia(query) {
  const get = () => typeof window !== "undefined" && window.matchMedia(query).matches;
  const [match, setMatch] = useState(get);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const h = () => setMatch(mq.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, [query]);
  return match;
}

export const useIsMobile = () => useMedia("(max-width: 640px)");

export function useResource(fn, deps = []) {
  const [state, setState] = useState({ data: undefined, error: null, loading: true });
  const seq = useRef(0);
  const load = useCallback(({ silent = false } = {}) => {
    const id = ++seq.current;
    if (!silent) setState((s) => ({ ...s, loading: true, error: null }));
    return Promise.resolve()
      .then(fn)
      .then((data) => {
        if (id === seq.current) setState({ data, error: null, loading: false });
        return data;
      })
      .catch((error) => {
        if (id === seq.current) setState((s) => ({ data: silent ? s.data : undefined, error, loading: false }));
      });
  }, deps);
  useEffect(() => { load(); }, [load]);
  useEffect(() => onRetry(() => load({ silent: true })), [load]);
  return { ...state, reload: load, setData: (data) => setState((s) => ({ ...s, data })) };
}
