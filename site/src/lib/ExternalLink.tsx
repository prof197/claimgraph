import type { ReactNode } from "react";

export const REPOSITORY_URL = "https://github.com/prof197/claimgraph";

/** A link off the page: it opens in a new tab, says so to screen readers, and gives the new
 * page no handle back on this one. */
export function ExternalLink({
  href,
  className,
  children,
}: {
  href: string;
  className?: string | undefined;
  children: ReactNode;
}) {
  return (
    <a href={href} className={className} target="_blank" rel="noopener noreferrer">
      {children}{" "}
      <span className="visually-hidden">(opens in a new tab)</span>
    </a>
  );
}
