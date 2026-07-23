import { ActionIcon, Anchor, Group, Switch, TextInput } from '@mantine/core';
import { IconStar, IconTrash } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

export interface AffiliationRowValues {
  title: string | null;
  is_primary: boolean;
  is_power_partner: boolean;
}

interface AffiliationRowProps {
  /** The other side of the affiliation — a venue name (on a contact page) or a contact name. */
  label: string;
  linkTo: string;
  values: AffiliationRowValues;
  onSave: (values: AffiliationRowValues) => void;
  onRemove: () => void;
}

/** One affiliation, inline-editable. Holds an optimistic local copy so a switch flips immediately
 *  and every write carries the full current row state — toggling one flag never clobbers another
 *  with a stale prop. Re-syncs from props when the server data changes (e.g. this row is demoted
 *  because a sibling became primary). Used by both the venue and contact detail pages. */
export function AffiliationRow({ label, linkTo, values, onSave, onRemove }: AffiliationRowProps) {
  const [row, setRow] = useState(values);
  const [titleDraft, setTitleDraft] = useState(values.title ?? '');

  useEffect(() => {
    setRow(values);
    setTitleDraft(values.title ?? '');
  }, [values.title, values.is_primary, values.is_power_partner]);

  function commit(next: AffiliationRowValues) {
    setRow(next); // optimistic
    onSave(next);
  }

  function saveTitle() {
    const next = titleDraft.trim() ? titleDraft.trim() : null;
    if (next !== row.title) {
      commit({ ...row, title: next });
    }
  }

  return (
    <Group gap="md" wrap="nowrap" justify="space-between">
      <Anchor component={Link} to={linkTo} fw={500} style={{ minWidth: 140 }}>
        {label}
      </Anchor>
      <TextInput
        size="xs"
        placeholder="Role / title"
        aria-label={`Title for ${label}`}
        value={titleDraft}
        onChange={(event) => setTitleDraft(event.currentTarget.value)}
        onBlur={saveTitle}
        style={{ flex: 1 }}
      />
      <Switch
        size="xs"
        label="Primary"
        checked={row.is_primary}
        onChange={(event) => commit({ ...row, is_primary: event.currentTarget.checked })}
      />
      <Switch
        size="xs"
        label="Power partner"
        checked={row.is_power_partner}
        onChange={(event) => commit({ ...row, is_power_partner: event.currentTarget.checked })}
        thumbIcon={row.is_power_partner ? <IconStar size={10} /> : undefined}
      />
      <ActionIcon variant="subtle" color="red" onClick={onRemove} aria-label={`Remove ${label}`}>
        <IconTrash size={16} />
      </ActionIcon>
    </Group>
  );
}