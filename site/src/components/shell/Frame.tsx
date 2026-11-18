import { ExternalLink, REPOSITORY_URL } from "../../lib/ExternalLink";
import type { MouseEvent, ReactNode } from "react";
import "./shell.css";

/** A link to a section of this page that scrolls to it and focuses its heading, leaving the
 * address as it is. */
export function JumpLink({ to, children }: { to: string; children: ReactNode }) {
  const jump = (event: MouseEvent<HTMLAnchorElement>) => {
    const target = document.getElementById(to);
    if (target === null) {
      return;
    }
    event.preventDefault();
    target.scrollIntoView?.({ block: "start" });
    target.focus({ preventScroll: true });
  };
  return (
    <a href={`#${to}`} onClick={jump}>
      {children}
    </a>
  );
}

export function PageHeader() {
  return (
    <header className="shell-header">
      <div className="shell-header__inner">
        <span className="shell-wordmark">Claimgraph</span>
        <nav aria-label="Site" className="shell-header__nav">
          <JumpLink to="method-h">
            How it works
          </JumpLink>
          <JumpLink to="evals-h">
            Evals
          </JumpLink>
          <ExternalLink href={REPOSITORY_URL}>Source code</ExternalLink>
        </nav>
      </div>
    </header>
  );
}

export function PageFooter() {
  return (
    <footer className="shell-footer">
      <div className="shell-footer__inner">
        <p className="shell-footer__note">Research navigation demo, not medical advice.</p>
        <p className="shell-small shell-muted">
          Abstracts from PubMed via NCBI E-utilities ·{" "}
          <ExternalLink href={REPOSITORY_URL}>Open source on GitHub</ExternalLink>
        </p>
      </div>
    </footer>
  );
}
