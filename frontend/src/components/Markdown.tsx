import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";

/** Assistant markdown. Inline `code` that names a known device/testbed becomes a link. */
export default function Markdown({ text, links }: { text: string; links: Map<string, string> }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) =>
          href && href.startsWith("/") ? (
            <Link to={href} className="text-accent underline-offset-2 hover:underline">
              {children}
            </Link>
          ) : (
            <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent underline-offset-2 hover:underline">
              {children}
            </a>
          ),
        code: ({ children, className }) => {
          const value = String(children ?? "");
          const to = !className ? links.get(value.trim().toLowerCase()) : undefined;
          if (to) {
            return (
              <Link to={to} className="rounded bg-accent-soft px-1 font-mono text-[0.85em] text-accent hover:underline">
                {value}
              </Link>
            );
          }
          return <code className={className}>{children}</code>;
        },
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
