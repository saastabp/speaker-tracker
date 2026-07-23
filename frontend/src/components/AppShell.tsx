import {
  AppShell as MantineAppShell,
  Button,
  Group,
  Loader,
  NavLink,
  ScrollArea,
  Text,
} from '@mantine/core';
import {
  IconBuilding,
  IconColumns,
  IconHistory,
  IconLayoutDashboard,
  IconMail,
  IconMicrophone,
  IconTarget,
  IconTemplate,
  IconUsers,
  type Icon,
} from '@tabler/icons-react';
import { NavLink as RouterNavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuthSession } from '../auth/session';
import { BRAND_CREAM } from '../theme';
import classes from './AppShell.module.css';

interface NavItem {
  label: string;
  to: string;
  icon: Icon;
}

// The app's information architecture (DESIGN §7). Only Dashboard has a real page in slice 1; the
// rest route to a placeholder until later slices.
const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard', to: '/', icon: IconLayoutDashboard },
  { label: 'Pipeline', to: '/pipeline', icon: IconColumns },
  { label: 'Venues', to: '/venues', icon: IconBuilding },
  { label: 'Contacts', to: '/contacts', icon: IconUsers },
  { label: 'Emails', to: '/emails', icon: IconMail },
  { label: 'History', to: '/history', icon: IconHistory },
  { label: 'Templates', to: '/templates', icon: IconTemplate },
  { label: 'Targets', to: '/targets', icon: IconTarget },
  { label: 'Talks', to: '/talks', icon: IconMicrophone },
];

function isActive(pathname: string, to: string): boolean {
  return to === '/' ? pathname === '/' : pathname.startsWith(to);
}

export function AppShell() {
  const { pathname } = useLocation();
  const { isAuthenticated, isLoading, user, signIn, signOut } = useAuthSession();

  return (
    <MantineAppShell header={{ height: 56 }} navbar={{ width: 240, breakpoint: 'sm' }} padding="md">
      <MantineAppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Text fw={700} c="navy.9">
            Speaker Tracker
          </Text>
          {isLoading ? (
            <Loader size="xs" />
          ) : isAuthenticated ? (
            <Group gap="sm">
              {user?.email && (
                <Text size="sm" c="dimmed">
                  {user.email}
                </Text>
              )}
              <Button variant="subtle" size="xs" onClick={signOut}>
                Sign out
              </Button>
            </Group>
          ) : (
            <Button size="xs" onClick={signIn}>
              Sign In
            </Button>
          )}
        </Group>
      </MantineAppShell.Header>

      <MantineAppShell.Navbar
        p="xs"
        style={{ backgroundColor: 'var(--mantine-color-navy-9)', border: 'none' }}
      >
        <ScrollArea>
          {NAV_ITEMS.map((item) => {
            const ItemIcon = item.icon;
            return (
              <NavLink
                key={item.to}
                component={RouterNavLink}
                to={item.to}
                active={isActive(pathname, item.to)}
                label={item.label}
                leftSection={<ItemIcon size={18} stroke={1.5} />}
                className={classes.navLink}
              />
            );
          })}
        </ScrollArea>
      </MantineAppShell.Navbar>

      <MantineAppShell.Main style={{ backgroundColor: BRAND_CREAM }}>
        {isLoading ? (
          <Group>
            <Loader size="sm" />
            <Text c="dimmed">Restoring your session…</Text>
          </Group>
        ) : isAuthenticated ? (
          <Outlet />
        ) : (
          <Text c="dimmed">Please sign in to view your pipeline.</Text>
        )}
      </MantineAppShell.Main>
    </MantineAppShell>
  );
}