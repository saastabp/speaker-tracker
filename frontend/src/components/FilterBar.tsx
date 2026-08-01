import { Button, Group, TextInput } from '@mantine/core';
import { IconSearch } from '@tabler/icons-react';
import type { ReactNode } from 'react';

export interface FilterPill {
  value: string;
  label: string;
  active: boolean;
}

interface FilterBarProps {
  search: string;
  onSearch: (value: string) => void;
  searchPlaceholder?: string;
  /** Pill toggles; the caller decides single- vs multi-select and computes each `active`. */
  pills?: FilterPill[];
  onPillClick?: (value: string) => void;
  /** A filter that pills cannot express, rendered at the end of the row. Pipeline's stage-reach
   *  Select uses it: one control standing for six ordered choices, where six more pills would
   *  restate the board's own columns and crowd the row. */
  extra?: ReactNode;
}

/** Reusable list toolbar: a search box plus pill filters (mockup `.toolbar`). Purely
 *  presentational — the page owns the filter state and the actual filtering. */
export function FilterBar({
  search,
  onSearch,
  searchPlaceholder = 'Search…',
  pills,
  onPillClick,
  extra,
}: FilterBarProps) {
  return (
    <Group gap="sm" wrap="wrap">
      <TextInput
        leftSection={<IconSearch size={16} />}
        placeholder={searchPlaceholder}
        value={search}
        onChange={(event) => onSearch(event.currentTarget.value)}
        w={240}
      />
      {pills?.map((pill) => (
        <Button
          key={pill.value}
          size="xs"
          radius="xl"
          variant={pill.active ? 'light' : 'default'}
          color={pill.active ? 'terracotta' : 'gray'}
          onClick={() => onPillClick?.(pill.value)}
        >
          {pill.label}
        </Button>
      ))}
      {extra}
    </Group>
  );
}