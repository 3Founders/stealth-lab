// This app does not install @types/react-dom. Popover.tsx needs only createPortal, so it is declared here.
declare module "react-dom" {
  export function createPortal(children: import("react").ReactNode, container: Element | DocumentFragment, key?: string | null): import("react").ReactPortal;
}
