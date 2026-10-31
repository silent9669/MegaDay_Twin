import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { usePanelResize } from "./usePanelResize";
it("bounds keyboard resizing and restores the chosen width after expansion", () => {
  const { result } = renderHook(() => usePanelResize(700, 420));
  const key = (value: string) => act(() => result.current.grip.onKeyDown({ key: value, preventDefault() {} } as never));
  key("ArrowLeft");
  expect(result.current.style?.width).toBe(732);
  act(() => result.current.setExpanded(true));
  expect(result.current.style?.width).toBe(window.innerWidth - 112);
  act(() => result.current.setExpanded(false));
  expect(result.current.style?.width).toBe(732);
  key("Home"); key("ArrowRight");
  expect(result.current.style?.width).toBe(420);
  key("End"); key("ArrowLeft");
  expect(result.current.style?.width).toBe(window.innerWidth - 112);
});
