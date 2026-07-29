export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`brand-mark${compact ? " brand-mark--compact" : ""}`} aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}
