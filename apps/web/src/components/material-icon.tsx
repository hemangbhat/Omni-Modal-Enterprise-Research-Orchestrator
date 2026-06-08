type MaterialIconProps = {
  name: string;
  className?: string;
  /** Render the filled variant of the glyph. */
  fill?: boolean;
  /** Pixel size for the glyph. */
  size?: number;
  style?: React.CSSProperties;
};

/**
 * Thin wrapper around Google Material Symbols (Outlined).
 * Matches the icon usage across the OMERO reference screens.
 */
export function MaterialIcon({ name, className, fill, size, style }: MaterialIconProps) {
  return (
    <span
      aria-hidden="true"
      className={`material-symbols-outlined${fill ? " icon-fill" : ""}${
        className ? ` ${className}` : ""
      }`}
      style={{ fontSize: size ? `${size}px` : undefined, ...style }}
    >
      {name}
    </span>
  );
}
