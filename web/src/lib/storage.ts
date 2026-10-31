// Per-viewer UI memory (09 §2.3: tiers 2/3 remember open state). localStorage can
// throw (private mode, blocked site data), so every access is wrapped.
import { useCallback, useState } from 'react';

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore: the toggle still works for this page view.
  }
}

export function useStoredBoolean(key: string, initial: boolean): [boolean, (next: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    const raw = read(key);
    return raw === null ? initial : raw === '1';
  });
  const set = useCallback(
    (next: boolean) => {
      setValue(next);
      write(key, next ? '1' : '0');
    },
    [key],
  );
  return [value, set];
}
