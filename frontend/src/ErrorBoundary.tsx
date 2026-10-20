import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

// React 19 still has no hook for catching render errors; a class component is the only
// way to build a boundary. One at the app root is enough: without it, any throw while
// rendering unmounts the whole tree to a blank page.
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="crash" role="alert">
        <h1>Something broke on the bench.</h1>
        <p>
          The page hit an error it couldn't recover from. Reloading usually fixes it — your
          saved loadouts are untouched.
        </p>
        <button className="save-btn" onClick={() => location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
