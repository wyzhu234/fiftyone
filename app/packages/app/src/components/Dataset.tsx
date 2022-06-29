import React, { useEffect } from "react";
import { useRecoilValue } from "recoil";
import styled from "styled-components";

import SamplesContainer from "./SamplesContainer";
import HorizontalNav from "../components/HorizontalNav";
import SampleModal from "./SampleModal";
import { modal } from "../recoil/atoms";

const Container = styled.div`
  height: 100%;
  display: flex;
  flex-direction: column;
`;

const Body = styled.div`
  width: 100%;
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
`;

function Dataset() {
  const isModalActive = Boolean(useRecoilValue(modal));

  return (
    <>
      {isModalActive && <SampleModal />}
      <Container>
        <HorizontalNav key={"nav"} />
        <Body key={"body"}>
          <SamplesContainer key={"samples"} />
        </Body>
      </Container>
    </>
  );
}

export default React.memo(Dataset);
