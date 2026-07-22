import { Stack, Text, Title } from '@mantine/core';
import { useLocation } from 'react-router-dom';

/** Rendered for nav sections that exist in the IA but arrive in a later slice. */
export function Placeholder() {
  const { pathname } = useLocation();
  return (
    <Stack>
      <Title order={2} c="navy.9">
        Coming soon
      </Title>
      <Text c="dimmed">
        The <b>{pathname}</b> section is scaffolded in the navigation but arrives in a later slice.
      </Text>
    </Stack>
  );
}