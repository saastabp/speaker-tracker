import {
  AppShell as MantineAppShell,
  Button,
  Divider,
  Group,
  Loader,
  NavLink,
  ScrollArea,
  Stack,
  Text,
} from '@mantine/core';
import {
  IconBellRinging,
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
import { versionLabel } from '../version';
import classes from './AppShell.module.css';

interface NavItem {
  label: string;
  to: string;
  icon: Icon;
}

interface NavSection {
  heading?: string;
  items: NavItem[];
}

// Information architecture per the approved mockup (DESIGN §7): an ungrouped top cluster, then
// Relationships / Outreach / Growth. "Talks & Materials" has no route yet and falls through to the
// catch-all Placeholder until its slice lands.
//
// The mockup's **Compose** entry is deliberately absent — declined 2026-07-26, not pending. It
// would be a second route to the Compose button already on the Emails page, and since the composer
// here is a modal rather than the mockup's dedicated page, a nav item could only open it by
// handing off through a query param. Compose from the person instead: ContactDetail carries the
// button, which is also where the recipient is unambiguous.
const NAV_SECTIONS: NavSection[] = [
  {
    items: [
      { label: 'Dashboard', to: '/', icon: IconLayoutDashboard },
      { label: 'Pipeline', to: '/pipeline', icon: IconColumns },
      { label: 'History', to: '/history', icon: IconHistory },
    ],
  },
  {
    heading: 'Relationships',
    items: [
      { label: 'Venues & Orgs', to: '/venues', icon: IconBuilding },
      { label: 'Contacts', to: '/contacts', icon: IconUsers },
    ],
  },
  {
    heading: 'Outreach',
    items: [
      { label: 'Emails', to: '/emails', icon: IconMail },
      { label: 'Templates', to: '/templates', icon: IconTemplate },
      { label: 'Follow-ups', to: '/follow-ups', icon: IconBellRinging },
    ],
  },
  {
    heading: 'Growth',
    items: [
      { label: 'Targets', to: '/targets', icon: IconTarget },
      { label: 'Talks & Materials', to: '/talks', icon: IconMicrophone },
    ],
  },
];

function isActive(pathname: string, to: string): boolean {
  return to === '/' ? pathname === '/' : pathname.startsWith(to);
}

/** The 360 Balanced Living wordmark, ported from the approved mockup, tuned for the navy rail. */
function BrandMark() {
  return (
    <div style={{ padding: '2px 6px 14px' }}>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 720 200"
        role="img"
        aria-label="360 Balanced Living"
        style={{ width: '100%', height: 'auto', display: 'block' }}
      >
        <g transform="translate(10,16) scale(0.45)">
          <path
            d="M178.5029,159.8372,147.3746,128.709a69,69,0,0,1,0-97.5808L178.5029,0l31.1282,31.1282a69,69,0,0,1,0,97.5808Z"
            fill="#E5B544"
          />
          <path
            d="M139.9775,181.8486h-32.72a69,69,0,0,1-69-69v-32.72h32.72a69,69,0,0,1,69,69Z"
            fill="#D88A4F"
          />
          <path
            d="M129.4681,220.3739l-15.9437,15.9437a69,69,0,0,1-97.5807,0L0,220.3739,15.9437,204.43a69,69,0,0,1,97.5807,0Z"
            fill="#C7723D"
          />
          <path
            d="M147.2973,251.5794v13.393a69,69,0,0,1-69,69H64.9043v-13.393a69,69,0,0,1,69-69Z"
            fill="#B8826E"
          />
          <path
            d="M178.5029,260.0921l6.2387,6.2387a65.3306,65.3306,0,0,1,0,92.3916l-6.2387,6.2388-6.2388-6.2388a65.3306,65.3306,0,0,1,0-92.3916Z"
            fill="#9CA0AC"
          />
          <path
            d="M203.7793,245.65H211.72a58.7976,58.7976,0,0,1,58.7977,58.7976v7.9407H262.577a58.7977,58.7977,0,0,1-58.7977-58.7977Z"
            fill="#7C92AE"
          />
          <path
            d="M210.6746,220.3739l5.0534-5.0534a52.9179,52.9179,0,0,1,74.8372,0l5.0534,5.0534-5.0534,5.0534a52.9179,52.9179,0,0,1-74.8372,0Z"
            fill="#5C7DA8"
          />
          <path
            d="M198.9768,199.9v-6.432a47.6261,47.6261,0,0,1,47.6261-47.6261h6.432v6.4319A47.6262,47.6262,0,0,1,205.4087,199.9Z"
            fill="#DCEBF5"
          />
        </g>
        <text
          x="190"
          y="118"
          fontFamily="Georgia,'Times New Roman',serif"
          fontSize="54"
          fontWeight="700"
          fontStyle="italic"
          fill="#F5F1E8"
        >
          360
          <tspan fontWeight="400" fontSize="40" fill="#E0935C" dx="10">
            Balanced Living
          </tspan>
        </text>
      </svg>
      <Text
        mt={8}
        pl={2}
        fw={700}
        style={{ fontSize: 11, letterSpacing: '0.16em', color: '#8FA3B2' }}
      >
        SPEAKER TRACKER
      </Text>
    </div>
  );
}

export function AppShell() {
  const { pathname } = useLocation();
  const { isAuthenticated, isLoading, user, signIn, signOut } = useAuthSession();

  return (
    <MantineAppShell navbar={{ width: 236, breakpoint: 'sm' }} padding="md">
      <MantineAppShell.Navbar
        p="md"
        style={{
          backgroundColor: 'var(--mantine-color-navy-9)',
          border: 'none',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <BrandMark />

        <ScrollArea style={{ flex: 1 }}>
          <Stack gap={2}>
            {NAV_SECTIONS.map((section, i) => (
              <div key={section.heading ?? `top-${i}`}>
                {section.heading && (
                  <Text
                    mt={i === 0 ? 0 : 12}
                    mb={4}
                    px={10}
                    tt="uppercase"
                    fw={700}
                    style={{ fontSize: 10.5, letterSpacing: '0.1em', color: '#7E93A1' }}
                  >
                    {section.heading}
                  </Text>
                )}
                {section.items.map((item) => {
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
              </div>
            ))}
          </Stack>
        </ScrollArea>

        <div style={{ marginTop: 'auto', paddingTop: 12 }}>
          <Divider color="rgba(255,255,255,0.10)" mb={10} />
          {isLoading ? (
            <Loader size="xs" color="gray.4" />
          ) : isAuthenticated ? (
            <Stack gap={4}>
              {user?.email && (
                <Text size="xs" style={{ color: '#7E93A1' }} truncate>
                  {user.email}
                </Text>
              )}
              <Button
                variant="subtle"
                color="gray"
                size="compact-xs"
                onClick={signOut}
                style={{ alignSelf: 'flex-start' }}
              >
                Sign out
              </Button>
            </Stack>
          ) : (
            <Button size="xs" fullWidth onClick={signIn}>
              Sign In
            </Button>
          )}
          {/* Below the sign-out control and outside the auth branches on purpose: which build this
              is stays answerable even on the signed-out screen, which is where a broken deploy is
              most likely to strand someone. */}
          {/* Same grey as the email above rather than something quieter: the one job this has is
              being readable in a screenshot of a problem. */}
          <Text size="xs" mt={8} style={{ color: '#7E93A1' }} truncate>
            {versionLabel()}
          </Text>
        </div>
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