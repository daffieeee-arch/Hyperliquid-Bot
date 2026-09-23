"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

import { Notice } from "./notice";

type PanelBoundaryProps = {
  title: string;
  children: ReactNode;
};

type PanelBoundaryState = {
  message: string | undefined;
};

/**
 * Keeps one panel's render failure from blanking the rest of the workspace.
 *
 * A down or gapped public chart must not take the capture quote table with it.
 */
export class PanelBoundary extends Component<PanelBoundaryProps, PanelBoundaryState> {
  override state: PanelBoundaryState = { message: undefined };

  static getDerivedStateFromError(error: unknown): PanelBoundaryState {
    return {
      message: error instanceof Error ? error.message : "This panel failed to render.",
    };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(this.props.title, error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.message !== undefined) {
      return (
        <Notice state="error" title={this.props.title}>
          {`${this.state.message} The rest of this screen stays on its own data.`}
        </Notice>
      );
    }
    return this.props.children;
  }
}
