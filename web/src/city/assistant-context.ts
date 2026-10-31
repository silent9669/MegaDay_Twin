import { createContext, useContext } from "react";
export const AssistantContext = createContext<{
  open: boolean;
  setOpen: (open: boolean) => void;
  width: number;
  setWidth: (width: number) => void;
  workspaceOpen: boolean;
  setWorkspaceOpen: (open: boolean) => void;
  setLauncherAnchor: (element: HTMLElement | null) => void;
  connected: boolean;
}>({
  open: false,
  setOpen: () => {},
  width: 400,
  setWidth: () => {},
  workspaceOpen: false,
  setWorkspaceOpen: () => {},
  setLauncherAnchor: () => {},
  connected: false,
});
export const useAssistant = () => useContext(AssistantContext);
