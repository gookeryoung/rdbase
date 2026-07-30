import { Card, Col, Row, Statistic } from "antd";
import { DatabaseOutlined, TableOutlined, SearchOutlined } from "@ant-design/icons";

// 仪表盘占位：统计卡片展示数据源数、表数、查询数（占位数据，P1 接入真实统计）
const Dashboard = () => {
  return (
    <Row gutter={16}>
      <Col span={8}>
        <Card>
          <Statistic title="数据源数" value={0} prefix={<DatabaseOutlined />} />
        </Card>
      </Col>
      <Col span={8}>
        <Card>
          <Statistic title="表数" value={0} prefix={<TableOutlined />} />
        </Card>
      </Col>
      <Col span={8}>
        <Card>
          <Statistic title="查询数" value={0} prefix={<SearchOutlined />} />
        </Card>
      </Col>
    </Row>
  );
};

export default Dashboard;
