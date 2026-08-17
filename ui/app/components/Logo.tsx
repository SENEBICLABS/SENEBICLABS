/**
 * Senebiclabs mark, the papyrus fan.
 *
 * The papyrus column capital is the defining architectural feature of
 * Alexandria-era Egyptian buildings. Multiple reeds, bound at the base,
 * fan outward at the top, each one a separate stem, all rising from one root.
 *
 * The Library of Alexandria stood on columns like this.
 * "Seneb", our name, is an Egyptian word. This mark comes from that world.
 *
 * Read as data: a histogram. The five columns form a bell curve ,
 * the exact shape of a healthy normal distribution.
 * The model learns this curve for every gene, in every cell type.
 * Deviation from this shape is disease.
 *
 * Read as botany: a lotus. Five petals, symmetric, rising from water.
 * The Egyptian symbol of creation and health.
 *
 * Read as signal: a frequency spectrum. Five measurement channels,
 * each carrying a different biological frequency.
 *
 * One base point. Five outputs. The shape of health.
  */

interface LogoProps {
  size?: number
}

export default function Logo({ size = 28 }: LogoProps) {
  return (
    <svg
      width={size}
      height={Math.round(size * 42 / 40)}
      viewBox="0 0 40 42"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="Senebiclabs"
      role="img"
    >
      {/* Five reeds from a single base, center tallest, outer shortest  */}
      <line   x1="20" y1="39" x2="20" y2="3"  stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <path d="M 20 39 C 15 30 11 18 11 8"     stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <path d="M 20 39 C 25 30 29 18 29 8"     stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <path d="M 20 39 C 12 35  5 27  5 18"    stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      <path d="M 20 39 C 28 35 35 27 35 18"    stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
      {/* Base node, the single origin  */}
      <circle cx="20" cy="39" r="2.5" fill="currentColor" />
      {/* Tip nodes, five output endpoints  */}
      <circle cx="20" cy="3"  r="2"   fill="currentColor" />
      <circle cx="11" cy="8"  r="2"   fill="currentColor" />
      <circle cx="29" cy="8"  r="2"   fill="currentColor" />
      <circle cx="5"  cy="18" r="2"   fill="currentColor" />
      <circle cx="35" cy="18" r="2"   fill="currentColor" />
    </svg>
  )
}
