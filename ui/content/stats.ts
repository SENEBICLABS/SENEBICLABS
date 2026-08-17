export interface StatEntry {
  value: string
  unit: string
  label: string
}

// Single source of truth for all reference model numbers.
// These appear in hero, engine console, and trust grid, update here only.
export const REFERENCE_STATS: Record<string, StatEntry> = {
  cells:     { value: '2.28', unit: 'M', label: 'Lung cells in the healthy reference' },
  cellTypes: { value: '60',   unit: '',  label: 'Cell types tracked' },
  donors:    { value: '268',  unit: '',  label: 'Healthy donors' },
  genes:     { value: '55',   unit: 'K', label: 'Genes profiled per cell' },
}
