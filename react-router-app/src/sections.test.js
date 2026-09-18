import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupItemsBySection, deduplicateArticles, sectionForItem } from './sections.js';

const item = (guid, category, title = guid, editionKind = 'print_edition') => ({
  id: guid + category, guid, categories: [category], title, editionKind,
});

test('daily briefs precede print contents; Online is last and retains newspaper membership', () => {
  const items = [item('biz', 'Business', 'Business online', 'online_only'),
    item('leader', 'Leaders'), item('brief', 'Briefing'), item('letter', 'Letters'),
    item('weekly', 'The World This Week'), item('world', 'The World in Brief'),
    item('us', 'In Brief', 'The US in brief: Today'), item('invite', 'By Invitation')];
  const groups = groupItemsBySection(items);
  assert.deepEqual(groups.map(g => g.name), ['The World in Brief', 'United States in Brief',
    'The World This Week', 'Leaders', 'Letters', 'By Invitation', 'Briefing', 'Business', 'Online']);
  assert.equal(groups.at(-1).items[0].guid, 'biz');
  assert.equal(groupItemsBySection(items, 'Business')[0].items[0].guid, 'biz');
  assert.deepEqual(groupItemsBySection(items, 'Missing'), []);
});

test('generic In Brief does not make an ordinary article a daily US briefing', () => {
  assert.equal(sectionForItem(item('inheritocracy', 'In Brief', 'Don’t fear the inheritocracy')), 'In Brief');
  assert.equal(sectionForItem(item('us', 'United States', 'United States in Brief')), 'United States in Brief');
});

test('same-GUID alias prefers a specific section regardless of arrival order', () => {
  const alias = item('shared-guid', 'In Brief', 'Don’t fear the inheritocracy', 'online_only');
  const canonical = item('shared-guid', 'By Invitation', 'Don’t fear the inheritocracy', 'online_only');
  for (const items of [[alias, canonical], [canonical, alias]]) {
    assert.deepEqual(deduplicateArticles(items), [canonical]);
    assert.deepEqual(groupItemsBySection(deduplicateArticles(items)).map(g => g.name), ['By Invitation', 'Online']);
  }
  assert.equal(deduplicateArticles([canonical, item('different-guid', 'By Invitation', canonical.title)]).length, 2);
});
