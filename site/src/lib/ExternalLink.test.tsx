import { render, screen } from "@testing-library/react";
import { ExternalLink } from "./ExternalLink";

it("opens in a new tab, says so, and leaves no handle on this page", () => {
  render(<ExternalLink href="https://example.org/">Open on PubMed</ExternalLink>);

  const link = screen.getByRole("link", { name: "Open on PubMed (opens in a new tab)" });
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
});
