import {Badge, Button, Card, Group, Stack, Text, Title} from '@mantine/core';
import {IconDownload, IconArrowUpRight} from '@tabler/icons-react';

type Release = Record<string, any>;

export function groupVersions(releases: Release[]) {
  const groups = new Map<string, Release>();
  for (const release of releases) {
    const key = String(release.version || '未标注版本');
    const group = groups.get(key);
    const downloads = (release.deliverables || []).map((item: Release) => ({
      ...item,
      platform: item.platform ?? release.platform ?? '',
      size: item.size ?? release.size ?? '',
      updated_at: item.updated_at || release.created_at,
    }));
    if (group) {
      group.deliverables.push(...downloads);
      if (release.notes && !group.notes.includes(release.notes)) group.notes.push(release.notes);
    } else {
      groups.set(key, {version: key, notes: release.notes ? [release.notes] : [], deliverables: downloads});
    }
  }
  return [...groups.values()];
}

function VersionCard({release}: {release: Release}) {
  return <Card withBorder className="release-card">
    <Group gap="xs"><Badge variant="filled">{release.version}</Badge></Group>
    {release.notes.map((note: string) => <Text size="sm" mt="xs" key={note} style={{whiteSpace: 'pre-wrap'}}>{note}</Text>)}
    <div className="download-list">
      {release.deliverables.filter((item: Release) => item.kind === 'file' ? item.filename : item.kind === 'url' && item.url).map((item: Release, index: number) => <div className="download-row" key={item.id || index}>
        <Text size="sm" fw={600}>{item.platform || '未指定端类型'}</Text>
        <div className="download-meta">
          {item.size && <Text size="xs" c="dimmed">{item.size}</Text>}
          {item.updated_at && <Text size="xs" c="dimmed">{new Date(item.updated_at).toLocaleDateString()}</Text>}
        </div>
        <Button size="xs" component="a" href={item.kind === 'file' ? `/api/download/${item.id}` : item.url}
          target={item.kind === 'url' ? '_blank' : undefined} rel={item.kind === 'url' ? 'noreferrer' : undefined}
          aria-label={`${item.platform || '安装包'} ${release.version} ${item.kind === 'file' ? '下载' : '打开网址'}`}
          leftSection={item.kind === 'file' ? <IconDownload size={14}/> : <IconArrowUpRight size={14}/>}>
          {item.kind === 'file' ? '下载' : '打开网址'}
        </Button>
      </div>)}
      {!release.deliverables.length && <Text size="sm" c="dimmed">暂无下载项</Text>}
    </div>
  </Card>;
}

export function ReleaseDownloads({name, releases, isProduct = false, hasSubapps = false}: {name: string; releases: Release[]; isProduct?: boolean; hasSubapps?: boolean}) {
  const versions = groupVersions(releases);
  if (isProduct && !versions.length && hasSubapps) return null;
  return <section className="release-section">
    <Title order={isProduct ? 2 : 3}>{isProduct ? '最新版本与下载' : name}</Title>
    {!versions.length ? <Text size="sm" c="dimmed" mt="sm">暂无可下载版本。</Text> : <>
      <Stack mt="sm" gap="xs"><VersionCard release={versions[0]}/></Stack>
      {versions.length > 1 && <details className="release-history"><summary>历史版本（{versions.length - 1}）</summary>
        <Stack mt="sm" gap="xs">{versions.slice(1).map(release => <VersionCard key={release.version} release={release}/>)}</Stack>
      </details>}
    </>}
  </section>;
}
