import { createContext, useContext } from 'react';
import type { TrackedProduct } from './types';
export type HQTab = 'overview'|'details'|'comparisons'|'activity';
export const HQSelection = createContext<{product:TrackedProduct|null;open:(product:TrackedProduct,tab:HQTab)=>void;tab:(tab:HQTab)=>void;log:(action:string,detail:string)=>void}|null>(null);
export const useHQSelection = () => useContext(HQSelection);
