import {useState} from 'react';
import {Button, Card, FileInput, Group, Select, SimpleGrid, Stack, Text, Textarea, TextInput} from '@mantine/core';

type D = Record<string, any>;
type UploadState = {percent: number; status: string};

export function ReleaseEditor({release, platforms, onUpdate, upload, uploadState}: {
  release: D; platforms: string[]; onUpdate: (update: (release: D) => D) => void;
  upload: (file: File) => Promise<D>; uploadState?: UploadState;
}) {
  const [packagePlatform, setPackagePlatform] = useState<string | null>(null);
  const selectedPlatform = packagePlatform || (platforms.length === 1 ? platforms[0] : '');
  const patch = (fields: D) => onUpdate(current => ({...current, ...fields}));
  const updateDownload = (index: number, fields: D) => onUpdate(current => ({...current, deliverables: (current.deliverables || []).map((item: D, i: number) => i === index ? {...item, ...fields} : item)}));
  const today = () => new Date().toLocaleDateString('sv-SE');
  return <Stack gap="sm">
    <TextInput label="版本号" placeholder="例如：1.2.0" required value={release.version || ''} onChange={e => patch({version: e.target.value})}/>
    <Textarea label="更新说明" autosize minRows={2} maxRows={12} value={release.notes || ''} onChange={e => patch({notes: e.target.value})}/>
    <Text size="sm" c="dimmed">一个版本可添加多个端的下载项，每项单独设置端类型、大小与更新时间。</Text>
    {(release.deliverables || []).map((item: D, index: number) => <Card withBorder padding="sm" key={item.id || item.object_key || index}>
      <SimpleGrid cols={{base: 1, sm: 3}}>
        <Select label="端类型" placeholder="先在软件信息中选择适用平台" required data={platforms} value={item.platform ?? release.platform ?? null} onChange={platform => updateDownload(index, {platform: platform || ''})}/>
        <TextInput label="包大小（可选）" placeholder="例如：25 MB" value={item.size ?? release.size ?? ''} onChange={e => updateDownload(index, {size: e.target.value})}/>
        <TextInput label="更新时间" type="date" required value={String(item.updated_at || release.created_at || today()).slice(0, 10)} onChange={e => updateDownload(index, {updated_at: e.target.value})}/>
      </SimpleGrid>
      {item.kind === 'url' ? <TextInput mt="xs" label="下载地址" type="url" required value={item.url || ''} onChange={e => updateDownload(index, {url: e.target.value})}/> : <Text size="sm" mt="xs" style={{overflowWrap: 'anywhere'}}>{item.filename || item.label}</Text>}
      <Group justify="flex-end" mt="xs"><Button type="button" size="xs" variant="subtle" color="red" onClick={() => onUpdate(current => ({...current, deliverables: current.deliverables.filter((_: D, i: number) => i !== index)}))}>移除下载项</Button></Group>
    </Card>)}
    <Card withBorder padding="sm">
    <Text size="sm" fw={600} mb="xs">为此版本添加不同端的安装包</Text>
    <Select label="安装包端类型" placeholder="先选择 Windows、macOS 等端类型，再上传安装包" data={platforms} value={selectedPlatform || null} onChange={setPackagePlatform} disabled={uploadState?.status === 'uploading'}/>
    {!platforms.length && <Text size="xs" c="orange" mt="xs">请先在上方软件信息中选择适用平台。</Text>}
    <FileInput label="上传该端安装包" description="每上传一个文件，新增一项；可切换端类型后继续上传。" mt="sm" placeholder="选择文件（最大 100 MB）" clearable disabled={!platforms.includes(selectedPlatform) || uploadState?.status === 'uploading'} onChange={async file => {
      if (!file) return;
      try {
        const result = await upload(file);
        onUpdate(current => ({...current, deliverables: [...(current.deliverables || []), {kind: 'file', label: '下载', filename: result.filename, object_key: result.object_key, platform: selectedPlatform, size: `${(file.size / 1024 / 1024).toFixed(2)} MB`, updated_at: today()}]}));
      } catch { /* The shared upload handler displays the error. */ }
    }}/>
    {uploadState && <Text size="sm" role="status" c={uploadState.status === 'error' ? 'red' : uploadState.status === 'done' ? 'green' : 'blue'}>{uploadState.status === 'uploading' ? `正在上传：${uploadState.percent}%` : uploadState.status === 'done' ? '上传完成，请保存软件' : '上传失败，请重试'}</Text>}
    </Card>
    <Button type="button" size="xs" variant="light" onClick={() => onUpdate(current => ({...current, deliverables: [...(current.deliverables || []), {kind: 'url', label: '打开网址', url: '', platform: platforms.length === 1 ? platforms[0] : '', size: '', updated_at: today()}]}))}>添加下载地址</Button>
  </Stack>;
}
