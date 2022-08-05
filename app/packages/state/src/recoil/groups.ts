import { atom, selector } from "recoil";
import { dataset } from "./atoms";

export const isGroup = selector<boolean>({
  key: "isGroup",
  get: ({ get }) => {
    return get(dataset)?.mediaType === "group";
  },
});

export const defaultGroupSlice = selector<string>({
  key: "defaultGroupSlice",
  get: ({ get }) => get(dataset).defaultGroupSlice,
});

export const groupSlice = atom<string>({
  key: "groupSlice",
  default: null,
});

export const groupSlices = selector<string[]>({
  key: "groupSlices",
  get: ({ get }) => {
    return get(dataset)
      .groupMediaTypes.filter(({ mediaType }) => mediaType !== "point_cloud")
      .map(({ name }) => name)
      .sort();
  },
});

export const isPinned = selector<boolean>({
  key: "isPinned",
  get: () => true,
});

export const pinnedGroupSlice = selector<string>({
  key: "pinnedGroupSlice",
  get: ({ get }) => {
    const { groupMediaTypes } = get(dataset);
    for (const { name, mediaType } of groupMediaTypes) {
      if (mediaType === "point_cloud") {
        return name;
      }
    }
  },
});

export const groupField = selector<string>({
  key: "groupField",
  get: ({ get }) => get(dataset).groupField,
});
